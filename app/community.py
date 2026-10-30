"""GamedevX community: classic web board (กระทู้/ความเห็น), ads & banners, public play of shared games,
and admin moderation."""
import io
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from PIL import Image, ImageOps

from . import auth, config, db, export, pipeline

router = APIRouter()
PAGES = Path(__file__).parent / "pages"


def _page(name: str) -> HTMLResponse:
    return HTMLResponse((PAGES / name).read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else ""))[:64]


# ======================================================================= helpers
def categories() -> list[str]:
    return [c.strip() for c in db.get_setting("board_categories").splitlines() if c.strip()]


def clean_text(text: str) -> str:
    """Apply the admin's bad-word list (one word per line or comma separated)."""
    words = [w.strip() for w in re.split(r"[\n,]", db.get_setting("bad_words")) if w.strip()]
    for w in words:
        text = re.sub(re.escape(w), "*" * len(w), text, flags=re.I)
    return text


async def save_board_image(file: UploadFile | None) -> str:
    if file is None or not file.filename:
        return ""
    raw = await file.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "รูปใหญ่เกิน 8MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((1600, 1600))
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "ไฟล์รูปไม่ถูกต้อง")
    config.BOARD_DIR.mkdir(parents=True, exist_ok=True)
    if img.mode in ("RGBA", "LA", "P"):
        name = f"p_{uuid.uuid4().hex[:14]}.png"
        img.convert("RGBA").save(config.BOARD_DIR / name, "PNG", optimize=True)
    else:
        name = f"p_{uuid.uuid4().hex[:14]}.jpg"
        img.convert("RGB").save(config.BOARD_DIR / name, "JPEG", quality=85)
    return name


def _del_board_image(name: str) -> None:
    if name:
        (config.BOARD_DIR / Path(name).name).unlink(missing_ok=True)


def _poster(request: Request, guest_name: str, honeypot: str) -> tuple[dict | None, str]:
    """Returns (user or None, guest display name). Raises if posting is not allowed."""
    u = auth.current_user(request)
    if u:
        return u, ""
    if db.get_setting("allow_guest_post") != "1":
        raise HTTPException(401, "กรุณาเข้าสู่ระบบก่อนโพสต์")
    if honeypot:
        raise HTTPException(400, "spam")
    g = re.sub(r"\s+", " ", guest_name or "").strip()[:30]
    if len(g) < 2:
        raise HTTPException(400, "ใส่ชื่อผู้โพสต์อย่างน้อย 2 ตัวอักษร")
    if db.one("SELECT id FROM users WHERE lower(name)=lower(?)", (g,)):
        raise HTTPException(400, "ชื่อนี้เป็นของสมาชิก กรุณาเข้าสู่ระบบ หรือใช้ชื่ออื่น")
    return None, g


def _name(uid: int | None, guest: str = "") -> str:
    if not uid:
        return guest or "Guest"
    u = db.one("SELECT name, email FROM users WHERE id=?", (uid,))
    return (u["name"] or u["email"].split("@")[0]) if u else "?"


def _topic_cover(t: dict) -> str | None:
    if t["kind"] != "game" or not t["project_id"]:
        return None
    p = db.load_project(t["project_id"])
    if not p or not p["story"]:
        return None
    for b in p["story"]["backgrounds"]:
        f = pipeline.bg_path(t["project_id"], b["id"])
        if f.exists():
            return f"/pub/{t['id']}/bgs/{f.name}?v={int(f.stat().st_mtime)}"
    return None


def _refresh_topic(tid: int) -> None:
    last = db.one("SELECT user_id, created_at, guest_name FROM comments WHERE topic_id=? AND hidden=0 "
                  "ORDER BY id DESC LIMIT 1", (tid,))
    n = db.one("SELECT COUNT(*) n FROM comments WHERE topic_id=? AND hidden=0", (tid,))["n"]
    db.ex("UPDATE topics SET reply_count=?, last_reply_at=?, last_reply_user=? WHERE id=?",
          (n, last["created_at"] if last else None, (last["user_id"] or -1) if last else None, tid))


def _last_reply_name(t: dict) -> str:
    if t["last_reply_user"] is None:
        return ""
    if t["last_reply_user"] == -1:
        last = db.one("SELECT guest_name FROM comments WHERE topic_id=? AND hidden=0 ORDER BY id DESC LIMIT 1",
                      (t["id"],))
        return (last or {}).get("guest_name") or "Guest"
    return _name(t["last_reply_user"])


# ======================================================================= banners / ads
BANNER_POS = ("logo", "header", "center", "left", "right")


def _banner_out(b: dict) -> dict:
    b = dict(b)
    b["image_url"] = f"/banner-img/{b['image']}" if b["image"] else ""
    b["go_url"] = f"/go/{b['id']}" if b["link_url"] else ""
    return b


@router.get("/api/banners")
def api_banners():
    now = time.time()
    rows = db.q("SELECT * FROM banners WHERE active=1 AND (starts_at IS NULL OR starts_at<=?) "
                "AND (ends_at IS NULL OR ends_at>=?) ORDER BY position, sort, id", (now, now))
    out: dict[str, list] = {p: [] for p in BANNER_POS}
    for r in rows:
        out.setdefault(r["position"], []).append(_banner_out(r))
    return out


@router.get("/go/{bid}")
def banner_click(bid: int):
    b = db.one("SELECT link_url FROM banners WHERE id=?", (bid,))
    if not b or not b["link_url"]:
        return RedirectResponse("/")
    db.ex("UPDATE banners SET clicks=clicks+1 WHERE id=?", (bid,))
    return RedirectResponse(b["link_url"], status_code=302)


@router.get("/banner-img/{name}")
def banner_img(name: str):
    f = config.BANNER_DIR / Path(name).name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/board-img/{name}")
def board_img(name: str):
    f = config.BOARD_DIR / Path(name).name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=86400"})


def _clean_link(url: str) -> str:
    url = (url or "").strip()[:500]
    if url and not (url.startswith("http://") or url.startswith("https://") or url.startswith("/")):
        url = "https://" + url
    return url


def _date(v: str) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return time.mktime(time.strptime(v[:10], "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(400, "รูปแบบวันที่ต้องเป็น YYYY-MM-DD")


async def _save_banner_file(file: UploadFile) -> str:
    raw = await file.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 8MB")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        raise HTTPException(400, "รองรับ png, jpg, gif, webp")
    try:
        Image.open(io.BytesIO(raw)).verify()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "ไฟล์ภาพไม่ถูกต้อง")
    fname = f"b_{uuid.uuid4().hex[:12]}{ext}"
    config.BANNER_DIR.mkdir(parents=True, exist_ok=True)
    (config.BANNER_DIR / fname).write_bytes(raw)
    return fname


@router.get("/api/admin/banners")
def admin_banners(request: Request):
    auth.require_admin(request)
    return {"banners": [_banner_out(b) for b in db.q("SELECT * FROM banners ORDER BY position, sort, id")]}


@router.post("/api/admin/banners")
async def admin_add_banner(request: Request, position: str = Form(...), link_url: str = Form(""),
                           title: str = Form(""), caption: str = Form(""), sort: int = Form(0),
                           starts: str = Form(""), ends: str = Form(""), file: UploadFile | None = File(None)):
    auth.require_admin(request)
    if position not in BANNER_POS:
        raise HTTPException(400, "ตำแหน่งไม่ถูกต้อง")
    fname = await _save_banner_file(file) if file is not None and file.filename else ""
    if not fname and not title.strip() and not caption.strip():
        raise HTTPException(400, "ใส่ภาพ หรือหัวข้อ/ข้อความ อย่างน้อยหนึ่งอย่าง")
    bid = db.ex("INSERT INTO banners(position, image, link_url, title, caption, sort, active, starts_at, ends_at, "
                "created_at) VALUES(?,?,?,?,?,?,1,?,?,?)",
                (position, fname, _clean_link(link_url), title.strip()[:120], caption.strip()[:2000], sort,
                 _date(starts), _date(ends), db.now()))
    return {"id": bid}


@router.post("/api/admin/banners/{bid}")
async def admin_edit_banner(bid: int, request: Request, position: str = Form(...), link_url: str = Form(""),
                            title: str = Form(""), caption: str = Form(""), sort: int = Form(0),
                            active: int = Form(1), starts: str = Form(""), ends: str = Form(""),
                            remove_image: int = Form(0), file: UploadFile | None = File(None)):
    auth.require_admin(request)
    b = db.one("SELECT * FROM banners WHERE id=?", (bid,))
    if not b:
        raise HTTPException(404)
    if position not in BANNER_POS:
        raise HTTPException(400, "ตำแหน่งไม่ถูกต้อง")
    fname = b["image"]
    if file is not None and file.filename:
        fname = await _save_banner_file(file)
        if b["image"]:
            (config.BANNER_DIR / b["image"]).unlink(missing_ok=True)
    elif remove_image and b["image"]:
        (config.BANNER_DIR / b["image"]).unlink(missing_ok=True)
        fname = ""
    db.ex("UPDATE banners SET position=?, image=?, link_url=?, title=?, caption=?, sort=?, active=?, starts_at=?, "
          "ends_at=? WHERE id=?",
          (position, fname, _clean_link(link_url), title.strip()[:120], caption.strip()[:2000], sort,
           int(bool(active)), _date(starts), _date(ends), bid))
    return {"ok": True}


@router.post("/api/admin/banners/{bid}/reset-clicks")
def admin_reset_clicks(bid: int, request: Request):
    auth.require_admin(request)
    db.ex("UPDATE banners SET clicks=0 WHERE id=?", (bid,))
    return {"ok": True}


@router.delete("/api/admin/banners/{bid}")
def admin_del_banner(bid: int, request: Request):
    auth.require_admin(request)
    b = db.one("SELECT image FROM banners WHERE id=?", (bid,))
    if b:
        if b["image"]:
            (config.BANNER_DIR / b["image"]).unlink(missing_ok=True)
        db.ex("DELETE FROM banners WHERE id=?", (bid,))
    return {"ok": True}


HISTORY_SEED = [
    ("ปี 2544", "วศิน ภิรมย์ ได้พัฒนาเกมไทยสามมิติ อัจฉริยะปราบปีศาจ เกมนี้ได้รับความสนใจจากสื่อมวลชนทุกแขนง "
                "ได้ลงหน้า 1 หนังสือพิมพ์ The Nation และหนังสือพิมพ์อื่นๆ หลายฉบับ รวมถึงได้ออกรายการโทรทัศน์มากมาย "
                "และได้เปิดเว็บไซต์ GamedevX ขึ้น เพื่อให้เป็นที่แลกเปลี่ยนความรู้สำหรับผู้ที่สนใจการพัฒนาเกม"),
    ("ปี 2545", "วศิน ภิรมย์ ได้จัดทำหลักสูตรการพัฒนาเกมสามมิติ (3D Games Developer) ขึ้นที่สถาบัน NetDesign "
                "ซึ่งได้รับความนิยมอย่างแพร่หลาย และนักเรียนได้มาร่วมสร้างผลงานเกมต่างๆ มากมาย"),
    ("ปี 2569", "GamedevX กลับมาอีกครั้งในยุค AI — สร้างเกม Visual Novel ได้ด้วยการเล่าไอเดีย "
                "แล้วแชร์ผลงานให้เพื่อนนักพัฒนาเกมได้เล่นและแลกเปลี่ยนความเห็นกัน"),
]


def seed_history() -> None:
    """First start only: fill the right column with GamedevX history + a welcome topic."""
    if db.get_setting("_history_seeded") != "1":
        if not db.one("SELECT id FROM banners LIMIT 1"):
            for i, (t, c) in enumerate(HISTORY_SEED):
                db.ex("INSERT INTO banners(position, image, link_url, title, caption, sort, active, created_at) "
                      "VALUES('right','', '', ?, ?, ?, 1, ?)", (t, c, i, db.now()))
        db.set_setting("_history_seeded", "1")
    if db.get_setting("_welcome_seeded") != "1":
        admin = db.one("SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1")
        if admin and not db.one("SELECT id FROM topics LIMIT 1"):
            cats = categories()
            db.ex("INSERT INTO topics(user_id, kind, title, body, category, pinned, created_at) "
                  "VALUES(?, 'talk', ?, ?, ?, 1, ?)",
                  (admin["id"], "เสิร์ฟร้อนๆ GamedevX New Generation กลับมาแล้ว!!",
                   "ยินดีต้อนรับกลับสู่ GamedevX ครับ\n\nกระดานนี้เป็นที่แลกเปลี่ยนความรู้ ถาม-ตอบ และแชร์ผลงานเกม "
                   "เหมือนสมัยก่อน และตอนนี้สมาชิกสร้างเกม Visual Novel ด้วย AI แล้วแชร์ให้เพื่อน ๆ เล่นในกระทู้ได้ทันที\n\n"
                   "ฝากตัวกันด้วยนะครับ\n— วศิน ภิรมย์",
                   cats[-1] if cats else "", db.now()))
            db.set_setting("_welcome_seeded", "1")


# ======================================================================= web board
@router.get("/api/board")
def api_board(request: Request, page: int = 1, kind: str = "", q: str = "", cat: str = ""):
    me = auth.current_user(request)
    per = max(10, min(100, db.get_int_setting("board_per_page") or 30))
    page = max(1, page)
    where, args = [], []
    if not (me and me["is_admin"]):
        where.append("hidden=0")
    if kind in ("game", "talk"):
        where.append("kind=?")
        args.append(kind)
    if cat:
        where.append("category=?")
        args.append(cat)
    if q.strip():
        where.append("(title LIKE ? OR body LIKE ?)")
        args += [f"%{q.strip()}%", f"%{q.strip()}%"]
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.one(f"SELECT COUNT(*) n FROM topics {w}", tuple(args))["n"]
    rows = db.q(f"SELECT id, user_id, guest_name, kind, title, category, image, pinned, locked, hidden, views, "
                f"reply_count, last_reply_at, last_reply_user, created_at, project_id FROM topics {w} "
                f"ORDER BY pinned DESC, COALESCE(last_reply_at, created_at) DESC LIMIT ? OFFSET ?",
                (*args, per, (page - 1) * per))
    for r in rows:
        r["author"] = _name(r["user_id"], r["guest_name"])
        r["is_guest"] = not r["user_id"]
        r["last_reply_name"] = _last_reply_name(r)
        r["cover"] = _topic_cover(r)
        r["has_photo"] = bool(r["image"]) or bool(r["cover"])
    return {"topics": rows, "total": total, "page": page, "pages": max(1, (total + per - 1) // per),
            "categories": categories(), "allow_guest": db.get_setting("allow_guest_post") == "1"}


@router.post("/api/board")
async def api_board_post(request: Request, kind: str = Form("talk"), title: str = Form(""), body: str = Form(""),
                         category: str = Form(""), project_id: str = Form(""), guest_name: str = Form(""),
                         website: str = Form(""), file: UploadFile | None = File(None)):
    u, guest = _poster(request, guest_name, website)
    auth.rate_limit(request, "post", limit=10, window=3600)
    kind = "game" if kind == "game" else "talk"
    title = clean_text(title.strip()[:150])
    text = clean_text(body.strip()[:10000])
    cats = categories()
    if category not in cats:
        category = cats[0] if cats else ""
    pid = None
    if kind == "game":
        if not u:
            raise HTTPException(401, "กรุณาเข้าสู่ระบบเพื่อแชร์เกม")
        try:
            pid = int(project_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "เลือกเกมที่จะแชร์")
        p = db.load_project(pid)
        if not p or p["user_id"] != u["id"] or not p["story"]:
            raise HTTPException(400, "ไม่พบเกมนี้ในบัญชีของคุณ")
        title = title or p["story"]["title"]
        old = db.one("SELECT id FROM topics WHERE project_id=? AND kind='game'", (pid,))
        if old:
            db.ex("UPDATE topics SET title=?, body=?, category=?, edited_at=? WHERE id=?",
                  (title, text, category, db.now(), old["id"]))
            return {"id": old["id"], "updated": True}
    if len(title) < 3:
        raise HTTPException(400, "หัวข้อกระทู้สั้นเกินไป")
    img = await save_board_image(file)
    tid = db.ex("INSERT INTO topics(user_id, guest_name, kind, title, body, category, image, project_id, ip, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (u["id"] if u else 0, guest, kind, title, text, category, img, pid, _ip(request), db.now()))
    return {"id": tid}


def _visible_topic(request: Request, tid: int) -> tuple[dict, dict | None]:
    t = db.one("SELECT * FROM topics WHERE id=?", (tid,))
    me = auth.current_user(request)
    if not t or (t["hidden"] and not (me and (me["is_admin"] or me["id"] == t["user_id"]))):
        raise HTTPException(404, "ไม่พบกระทู้ หรือกระทู้ถูกซ่อนโดยผู้ดูแล")
    return t, me


@router.get("/api/board/{tid}")
def api_topic(tid: int, request: Request):
    t, me = _visible_topic(request, tid)
    db.ex("UPDATE topics SET views=views+1 WHERE id=?", (tid,))
    t["views"] += 1
    t["author"] = _name(t["user_id"], t["guest_name"])
    t["is_guest"] = not t["user_id"]
    t["cover"] = _topic_cover(t)
    t["image_url"] = f"/board-img/{t['image']}" if t["image"] else ""
    is_admin = bool(me and me["is_admin"])
    t["can_manage"] = bool(me and (me["id"] == t["user_id"] or is_admin))
    t["is_admin"] = is_admin
    t.pop("ip", None) if not is_admin else None
    t["synopsis"] = ""
    if t["kind"] == "game" and t["project_id"]:
        p = db.load_project(t["project_id"])
        if p and p["story"]:
            t["synopsis"] = p["story"]["synopsis"]
    cs = db.q("SELECT id, user_id, guest_name, body, image, hidden, ip, created_at, edited_at FROM comments "
              "WHERE topic_id=? ORDER BY id", (tid,))
    out = []
    for c in cs:
        if c["hidden"] and not is_admin:
            continue
        c["author"] = _name(c["user_id"], c["guest_name"])
        c["is_guest"] = not c["user_id"]
        c["image_url"] = f"/board-img/{c['image']}" if c["image"] else ""
        c["can_delete"] = bool(me and (me["id"] == c["user_id"] or is_admin))
        if not is_admin:
            c.pop("ip", None)
        out.append(c)
    return {"topic": t, "comments": out, "me": auth.public_user(me) if me else None,
            "allow_guest": db.get_setting("allow_guest_post") == "1"}


@router.post("/api/board/{tid}/comments")
async def api_comment(tid: int, request: Request, body: str = Form(""), guest_name: str = Form(""),
                      website: str = Form(""), file: UploadFile | None = File(None)):
    t, _ = _visible_topic(request, tid)
    if t["locked"]:
        raise HTTPException(400, "กระทู้นี้ถูกล็อก ไม่สามารถตอบได้")
    u, guest = _poster(request, guest_name, website)
    auth.rate_limit(request, "comment", limit=30, window=3600)
    text = clean_text(body.strip()[:6000])
    img = await save_board_image(file)
    if len(text) < 2 and not img:
        raise HTTPException(400, "พิมพ์ความคิดเห็นก่อนนะครับ")
    cid = db.ex("INSERT INTO comments(topic_id, user_id, guest_name, body, image, ip, created_at) "
                "VALUES(?,?,?,?,?,?,?)", (tid, u["id"] if u else 0, guest, text, img, _ip(request), db.now()))
    _refresh_topic(tid)
    return {"id": cid}


@router.delete("/api/board/{tid}")
def api_topic_delete(tid: int, request: Request):
    u = auth.require_user(request)
    t = db.one("SELECT user_id, image FROM topics WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    if t["user_id"] != u["id"] and not u["is_admin"]:
        raise HTTPException(403, "ลบได้เฉพาะเจ้าของกระทู้หรือแอดมิน")
    delete_topic(tid)
    return {"ok": True}


def delete_topic(tid: int) -> None:
    t = db.one("SELECT image FROM topics WHERE id=?", (tid,))
    for c in db.q("SELECT image FROM comments WHERE topic_id=?", (tid,)):
        _del_board_image(c["image"])
    if t:
        _del_board_image(t["image"])
    db.ex("DELETE FROM comments WHERE topic_id=?", (tid,))
    db.ex("DELETE FROM topics WHERE id=?", (tid,))


@router.delete("/api/board/comments/{cid}")
def api_comment_delete(cid: int, request: Request):
    u = auth.require_user(request)
    c = db.one("SELECT * FROM comments WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    if c["user_id"] != u["id"] and not u["is_admin"]:
        raise HTTPException(403)
    _del_board_image(c["image"])
    db.ex("DELETE FROM comments WHERE id=?", (cid,))
    _refresh_topic(c["topic_id"])
    return {"ok": True}


@router.post("/api/board/{tid}/pin")
def api_topic_pin(tid: int, request: Request, body: dict = Body(...)):
    auth.require_admin(request)
    db.ex("UPDATE topics SET pinned=? WHERE id=?", (int(bool(body.get("pinned"))), tid))
    return {"ok": True}


# ---- admin moderation
@router.get("/api/admin/board/topics")
def admin_topics(request: Request, q: str = "", page: int = 1):
    auth.require_admin(request)
    like = f"%{q.strip()}%"
    rows = db.q("SELECT t.*, u.name AS uname, u.email FROM topics t LEFT JOIN users u ON u.id=t.user_id "
                "WHERE t.title LIKE ? OR t.body LIKE ? OR u.name LIKE ? OR t.guest_name LIKE ? OR t.ip LIKE ? "
                "ORDER BY t.id DESC LIMIT 100 OFFSET ?", (like, like, like, like, like, (max(1, page) - 1) * 100))
    return {"topics": rows, "categories": categories()}


@router.put("/api/admin/board/topics/{tid}")
def admin_edit_topic(tid: int, request: Request, body: dict = Body(...)):
    auth.require_admin(request)
    t = db.one("SELECT * FROM topics WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    fields = {}
    for k in ("title", "body", "category"):
        if k in body:
            fields[k] = str(body[k])[:10000 if k == "body" else 150]
    for k in ("pinned", "locked", "hidden"):
        if k in body:
            fields[k] = int(bool(body[k]))
    if body.get("remove_image") and t["image"]:
        _del_board_image(t["image"])
        fields["image"] = ""
    if fields:
        fields["edited_at"] = db.now()
        db.ex(f"UPDATE topics SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), tid))
    return {"ok": True}


@router.delete("/api/admin/board/topics/{tid}")
def admin_del_topic(tid: int, request: Request):
    auth.require_admin(request)
    delete_topic(tid)
    return {"ok": True}


@router.get("/api/admin/board/comments")
def admin_comments(request: Request, q: str = ""):
    auth.require_admin(request)
    like = f"%{q.strip()}%"
    return {"comments": db.q(
        "SELECT c.*, u.name AS uname, t.title AS topic_title FROM comments c LEFT JOIN users u ON u.id=c.user_id "
        "LEFT JOIN topics t ON t.id=c.topic_id WHERE c.body LIKE ? OR u.name LIKE ? OR c.guest_name LIKE ? "
        "OR c.ip LIKE ? ORDER BY c.id DESC LIMIT 150", (like, like, like, like))}


@router.put("/api/admin/board/comments/{cid}")
def admin_edit_comment(cid: int, request: Request, body: dict = Body(...)):
    auth.require_admin(request)
    c = db.one("SELECT * FROM comments WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    fields = {}
    if "body" in body:
        fields["body"] = str(body["body"])[:6000]
    if "hidden" in body:
        fields["hidden"] = int(bool(body["hidden"]))
    if body.get("remove_image") and c["image"]:
        _del_board_image(c["image"])
        fields["image"] = ""
    if fields:
        fields["edited_at"] = db.now()
        db.ex(f"UPDATE comments SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), cid))
        _refresh_topic(c["topic_id"])
    return {"ok": True}


@router.delete("/api/admin/board/comments/{cid}")
def admin_del_comment(cid: int, request: Request):
    auth.require_admin(request)
    c = db.one("SELECT * FROM comments WHERE id=?", (cid,))
    if c:
        _del_board_image(c["image"])
        db.ex("DELETE FROM comments WHERE id=?", (cid,))
        _refresh_topic(c["topic_id"])
    return {"ok": True}


# ======================================================================= public play of shared games
def _game_topic(tid: int) -> dict:
    t = db.one("SELECT * FROM topics WHERE id=? AND kind='game' AND hidden=0", (tid,))
    if not t or not t["project_id"]:
        raise HTTPException(404, "ไม่พบเกม")
    p = db.load_project(t["project_id"])
    if not p or not p["story"]:
        raise HTTPException(404, "ไม่พบเกม")
    return {"topic": t, "project": p}


@router.get("/api/public/game/{tid}")
def api_public_game(tid: int):
    g = _game_topic(tid)
    p, pid = g["project"], g["project"]["id"]
    assets = export.collect_assets(pid, p["story"], p["music_map"])
    v = lambda f: int(f.stat().st_mtime)  # noqa: E731
    music = {}
    for k in assets["music"]:
        e = p["music_map"].get(k)
        music[k] = f"/pub/{tid}/libmusic/{e['id']}" if e["type"] == "lib" else f"/pub/{tid}/music/{e['file']}"
    return {"format": "gamedevx-story/1", "story": p["story"], "assets": {
        "chars": {k: f"/pub/{tid}/chars/{f.name}?v={v(f)}" for k, f in assets["chars"].items()},
        "bgs": {k: f"/pub/{tid}/bgs/{f.name}?v={v(f)}" for k, f in assets["bgs"].items()},
        "music": music}}


@router.get("/pub/{tid}/{folder}/{name}")
def pub_file(tid: int, folder: str, name: str):
    p = _game_topic(tid)["project"]
    if folder == "libmusic":
        used = {str(e.get("id")) for e in p["music_map"].values() if e.get("type") == "lib"}
        if name not in used:
            raise HTTPException(404)
        t = db.one("SELECT filename FROM music_tracks WHERE id=?", (int(name),))
        f = config.MUSIC_DIR / t["filename"] if t else None
    elif folder in ("chars", "bgs", "music"):
        f = pipeline.project_dir(p["id"]) / folder / Path(name).name
    else:
        raise HTTPException(404)
    if not f or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/board/{tid}", response_class=HTMLResponse)
def p_topic(tid: int):
    return _page("topic.html")


@router.get("/board", response_class=HTMLResponse)
def p_board():
    return RedirectResponse("/#board")
