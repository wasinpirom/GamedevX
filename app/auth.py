"""Accounts, sessions and permission helpers."""
import re
import time
from collections import defaultdict

import bcrypt
from fastapi import HTTPException, Request

from . import config, db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def check_pw(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], h.encode())
    except ValueError:
        return False


def public_user(u: dict) -> dict:
    return {k: u[k] for k in ("id", "email", "name", "is_admin", "credits", "created_at")}


def current_user(request: Request) -> dict | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not u or u["disabled"]:
        request.session.clear()
        return None
    return u


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "กรุณาเข้าสู่ระบบ")
    return u


def require_admin(request: Request) -> dict:
    u = require_user(request)
    if not u["is_admin"]:
        raise HTTPException(403, "สำหรับผู้ดูแลระบบเท่านั้น")
    return u


def create_user(email: str, password: str, name: str, is_admin: bool = False) -> dict:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "อีเมลไม่ถูกต้อง")
    if len(password) < 8:
        raise HTTPException(400, "รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร")
    if db.one("SELECT id FROM users WHERE email=?", (email,)):
        raise HTTPException(400, "อีเมลนี้สมัครไว้แล้ว")
    name = re.sub(r"\s+", " ", (name or "").strip())[:30]
    if name:
        if len(name) < 2 or "@" in name:
            raise HTTPException(400, "username ต้องยาว 2-30 ตัวอักษร และห้ามมี @")
        if db.one("SELECT id FROM users WHERE lower(name)=lower(?)", (name,)):
            raise HTTPException(400, "username นี้มีคนใช้แล้ว")
    else:
        base = email.split("@")[0][:24] or "member"
        name, i = base, 2
        while db.one("SELECT id FROM users WHERE lower(name)=lower(?)", (name,)):
            name, i = f"{base}{i}", i + 1
    uid = db.ex("INSERT INTO users(email, name, password_hash, is_admin, credits, created_at) VALUES(?,?,?,?,0,?)",
                (email, name, hash_pw(password), int(is_admin), db.now()))
    welcome = db.get_int_setting("welcome_credits")
    if welcome > 0:
        db.change_credits(uid, welcome, "เครดิตต้อนรับสมาชิกใหม่")
    return db.one("SELECT * FROM users WHERE id=?", (uid,))


def bootstrap_admin() -> None:
    """Create / promote the admin account from ADMIN_EMAIL + ADMIN_PASSWORD env vars."""
    if not config.ADMIN_EMAIL:
        return
    u = db.one("SELECT * FROM users WHERE email=?", (config.ADMIN_EMAIL,))
    if u:
        if not u["is_admin"]:
            db.ex("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
        return
    if config.ADMIN_PASSWORD and len(config.ADMIN_PASSWORD) >= 8:
        create_user(config.ADMIN_EMAIL, config.ADMIN_PASSWORD, "Admin", is_admin=True)


# ---- tiny in-memory rate limiter for login/register ----
_hits: dict[str, list[float]] = defaultdict(list)


def rate_limit(request: Request, key: str, limit: int = 10, window: int = 300) -> None:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "?")
    k = f"{key}:{ip}"
    now = time.time()
    _hits[k] = [t for t in _hits[k] if now - t < window]
    if len(_hits[k]) >= limit:
        raise HTTPException(429, "ลองบ่อยเกินไป กรุณารอสักครู่")
    _hits[k].append(now)
