"""SQLite storage. Single file at DATA_DIR/gamedevx.db (mount /data as a volume)."""
import json
import sqlite3
import threading
import time
from typing import Any

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    credits INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS credit_tx (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    admin_id INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_user ON credit_tx(user_id, created_at);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    brief TEXT NOT NULL DEFAULT '{}',
    story TEXT,
    music_map TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    cost INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    progress TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    refunded INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at);
CREATE TABLE IF NOT EXISTS music_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    mood TEXT NOT NULL,
    filename TEXT NOT NULL,
    credit TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS banners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position TEXT NOT NULL,          -- header | center | left | right
    image TEXT NOT NULL DEFAULT '',  -- filename in DATA_DIR/banners ('' = text only)
    link_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'talk',   -- game | talk
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    project_id INTEGER,
    pinned INTEGER NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0,
    reply_count INTEGER NOT NULL DEFAULT 0,
    last_reply_at REAL,
    last_reply_user INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topics_order ON topics(pinned, created_at);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_topic ON comments(topic_id, id);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
    return _conn


# columns added after v1 (ALTER TABLE keeps existing data)
MIGRATIONS = {
    "topics": [("category", "TEXT NOT NULL DEFAULT ''"), ("image", "TEXT NOT NULL DEFAULT ''"),
               ("locked", "INTEGER NOT NULL DEFAULT 0"), ("hidden", "INTEGER NOT NULL DEFAULT 0"),
               ("guest_name", "TEXT NOT NULL DEFAULT ''"), ("ip", "TEXT NOT NULL DEFAULT ''"),
               ("edited_at", "REAL")],
    "comments": [("image", "TEXT NOT NULL DEFAULT ''"), ("guest_name", "TEXT NOT NULL DEFAULT ''"),
                 ("ip", "TEXT NOT NULL DEFAULT ''"), ("hidden", "INTEGER NOT NULL DEFAULT 0"),
                 ("edited_at", "REAL")],
    "banners": [("clicks", "INTEGER NOT NULL DEFAULT 0"), ("starts_at", "REAL"), ("ends_at", "REAL")],
}


def _migrate(c: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


def now() -> float:
    return time.time()


# ---------- settings ----------
def get_setting(key: str) -> str:
    row = one("SELECT value FROM settings WHERE key=?", (key,))
    if row is not None:
        return row["value"]
    return config.DEFAULT_SETTINGS.get(key, "")


def get_int_setting(key: str) -> int:
    try:
        return int(float(get_setting(key)))
    except ValueError:
        return int(config.DEFAULT_SETTINGS.get(key, "0") or 0)


def all_settings() -> dict[str, str]:
    out = dict(config.DEFAULT_SETTINGS)
    for r in q("SELECT key, value FROM settings"):
        out[r["key"]] = r["value"]
    return out


def set_setting(key: str, value: str) -> None:
    ex("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, value))


# ---------- credits (atomic) ----------
class InsufficientCredits(Exception):
    pass


def change_credits(user_id: int, amount: int, reason: str, admin_id: int | None = None,
                   allow_negative: bool = False) -> int:
    """Add (positive) or deduct (negative) credits atomically. Returns new balance."""
    with _lock:
        c = conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise ValueError("user not found")
            new_bal = row["credits"] + amount
            if new_bal < 0 and not allow_negative:
                raise InsufficientCredits()
            c.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, user_id))
            c.execute(
                "INSERT INTO credit_tx(user_id, amount, balance_after, reason, admin_id, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (user_id, amount, new_bal, reason, admin_id, now()))
            c.execute("COMMIT")
            return new_bal
        except Exception:
            c.execute("ROLLBACK")
            raise


# ---------- projects ----------
def load_project(pid: int) -> dict | None:
    p = one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return None
    p["brief"] = json.loads(p["brief"] or "{}")
    p["story"] = json.loads(p["story"]) if p["story"] else None
    p["music_map"] = json.loads(p["music_map"] or "{}")
    return p


def save_story(pid: int, story: dict) -> None:
    ex("UPDATE projects SET story=?, title=?, updated_at=? WHERE id=?",
       (json.dumps(story, ensure_ascii=False), story.get("title") or "ไม่มีชื่อ", now(), pid))


def save_music_map(pid: int, mm: dict) -> None:
    ex("UPDATE projects SET music_map=?, updated_at=? WHERE id=?",
       (json.dumps(mm, ensure_ascii=False), now(), pid))


def job_update(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    ex(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
