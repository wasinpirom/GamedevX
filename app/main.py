"""GamedevX — AI Visual Novel studio. FastAPI entry point."""
import asyncio
import io
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.middleware.sessions import SessionMiddleware

from . import auth, community, config, db, export, imaging, pipeline

APP_DIR = Path(__file__).parent
PAGES = APP_DIR / "pages"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    config.MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    config.BANNER_DIR.mkdir(parents=True, exist_ok=True)
    config.BOARD_DIR.mkdir(parents=True, exist_ok=True)
    db.conn()
    auth.bootstrap_admin()
    community.seed_history()
    pipeline.recover_interrupted_jobs()
    yield


app = FastAPI(title="GamedevX", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="gdx_session",
                   max_age=60 * 60 * 24 * 30, same_site="lax", https_only=config.COOKIE_SECURE)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


# ============================================================ pages
def page(name: str) -> HTMLResponse:
    return HTMLResponse((PAGES / name).read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def p_home():
    return page("index.html")


@app.get("/login", response_class=HTMLResponse)
def p_login():
    return page("login.html")


@app.get("/register", response_class=HTMLResponse)
def p_register():
    return page("login.html")


@app.get("/app", response_class=HTMLResponse)
def p_app(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login?next=/app")
    return page("app.html")


@app.get("/project/{pid}", response_class=HTMLResponse)
def p_project(pid: int, request: Request):
    if not auth.current_user(request):
        return RedirectResponse(f"/login?next=/project/{pid}")
    return page("project.html")


@app.get("/play/{pid}", response_class=HTMLResponse)
def p_play(pid: int, request: Request):
    if not auth.current_user(request):
        return RedirectResponse(f"/login?next=/play/{pid}")
    return page("play.html")


@app.get("/topup", response_class=HTMLResponse)
def p_topup():
    return page("topup.html")


@app.get("/admin", response_class=HTMLResponse)
def p_admin(request: Request):
    u = auth.current_user(request)
    if not u or not u["is_admin"]:
        return RedirectResponse("/login?next=/admin")
    return page("admin.html")


@app.get("/healthz")
def healthz():
    db.one("SELECT 1 AS ok")
    return {"ok": True}


# ============================================================ auth API
@app.post("/api/register")
def api_register(request: Request, body: dict = Body(...)):
    auth.rate_limit(request, "register", limit=5, window=3600)
    u = auth.create_user(str(body.get("email", "")), str(body.get("password", "")), str(body.get("name", "")))
    request.session["uid"] = u["id"]
    return auth.public_user(u)


@app.post("/api/login")
def api_login(request: Request, body: dict = Body(...)):
    auth.rate_limit(request, "login", limit=10, window=300)
    login = str(body.get("email") or body.get("username") or "").strip()
    u = db.one("SELECT * FROM users WHERE email=?", (login.lower(),)) or \
        db.one("SELECT * FROM users WHERE lower(name)=lower(?)", (login,))
    if not u or not auth.check_pw(str(body.get("password", "")), u["password_hash"]):
        raise HTTPException(400, "username/อีเมล หรือรหัสผ่านไม่ถูกต้อง")
    if u["disabled"]:
        raise HTTPException(403, "บัญชีนี้ถูกระงับ กรุณาติดต่อ LINE OA " + config.LINE_OA_ID)
    request.session["uid"] = u["id"]
    return auth.public_user(u)


@app.post("/api/logout")
def api_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.post("/api/change-password")
def api_change_password(request: Request, body: dict = Body(...)):
    u = auth.require_user(request)
    if not auth.check_pw(str(body.get("old", "")), u["password_hash"]):
        raise HTTPException(400, "รหัสผ่านเดิมไม่ถูกต้อง")
    new = str(body.get("new", ""))
    if len(new) < 8:
        raise HTTPException(400, "รหัสผ่านใหม่ต้องยาวอย่างน้อย 8 ตัวอักษร")
    db.ex("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_pw(new), u["id"]))
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    u = auth.current_user(request)
    return {"user": auth.public_user(u) if u else None}


@app.get("/api/public-config")
def api_public_config():
    s = db.all_settings()
    return {
        "line_oa": config.LINE_OA_ID,
        "line_url": "https://line.me/R/ti/p/" + config.LINE_OA_ID,
        "kway_url": config.KWAY_SHOP_URL,
        "packages": [p for p in s["topup_packages"].splitlines() if p.strip()],
        "announcement": s["announcement"],
        "site_tagline": s["site_tagline"],
        "history_title": s["history_title"],
        "site_title": s["site_title"],
        "left_title": s["left_title"],
        "costs": {k[5:]: int(float(v or 0)) for k, v in s.items() if k.startswith("cost_")},
        "welcome_credits": int(float(s["welcome_credits"] or 0)),
        "moods": config.MOODS,
        "expressions": config.EXPRESSIONS,
        "art_styles": {k: k for k in config.ART_STYLES},
        "mock": config.MOCK_AI,
    }


@app.get("/api/credits/history")
def api_credit_history(request: Request):
    u = auth.require_user(request)
    return {"balance": u["credits"],
            "items": db.q("SELECT amount, balance_after, reason, created_at FROM credit_tx WHERE user_id=? "
                          "ORDER BY id DESC LIMIT 100", (u["id"],))}


# ============================================================ projects API
def own_project(request: Request, pid: int) -> tuple[dict, dict]:
    u = auth.require_user(request)
    p = db.load_project(pid)
    if not p or (p["user_id"] != u["id"] and not u["is_admin"]):
        raise HTTPException(404, "ไม่พบโปรเจกต์")
    return u, p


def asset_status(pid: int, story: dict | None) -> dict:
    if not story:
        return {"chars": {}, "bgs": {}, "refs": {}}
    refs = {}
    for kind, items in (("char", story["characters"]), ("bg", story["backgrounds"])):
        for it in items:
            f = pipeline.ref_path(pid, kind, it["id"])
            if f.exists():
                refs[f"{kind}_{it['id']}"] = int(f.stat().st_mtime)
    chars, bgs = {}, {}
    for c in story["characters"]:
        for e in pipeline.used_expressions(story, c["id"]):
            f = pipeline.char_path(pid, c["id"], e)
            chars[f"{c['id']}_{e}"] = int(f.stat().st_mtime) if f.exists() else 0
    for b in story["backgrounds"]:
        f = pipeline.bg_path(pid, b["id"])
        bgs[b["id"]] = int(f.stat().st_mtime) if f.exists() else 0
    return {"chars": chars, "bgs": bgs, "refs": refs}


@app.get("/api/projects")
def api_projects(request: Request):
    u = auth.require_user(request)
    rows = db.q("SELECT id, title, created_at, updated_at, story IS NOT NULL AS has_story FROM projects "
                "WHERE user_id=? ORDER BY updated_at DESC", (u["id"],))
    for r in rows:
        f = None
        p = db.load_project(r["id"])
        if p["story"] and p["story"]["backgrounds"]:
            bp = pipeline.bg_path(r["id"], p["story"]["backgrounds"][0]["id"])
            if bp.exists():
                f = f"/files/{r['id']}/bgs/{bp.name}?v={int(bp.stat().st_mtime)}"
        r["cover"] = f
        r["running"] = bool(db.one("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')",
                                   (r["id"],)))
    return {"projects": rows, "credits": u["credits"]}


def _clean_brief(b: dict) -> dict:
    def clamp(v, lo, hi, d):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return d
    idea = str(b.get("idea", "")).strip()
    if len(idea) < 10:
        raise HTTPException(400, "เล่าไอเดียเกมอย่างน้อย 10 ตัวอักษร")
    return {
        "idea": idea[:3000],
        "genre": str(b.get("genre", ""))[:60],
        "art_style": b.get("art_style") if b.get("art_style") in config.ART_STYLES else "anime",
        "scenes": clamp(b.get("scenes"), 3, 15, 6),
        "characters": clamp(b.get("characters"), 1, 5, 2),
        "branching": bool(b.get("branching", True)),
        "language": "en" if b.get("language") == "en" else "th",
    }


@app.post("/api/projects")
def api_create_project(request: Request, body: dict = Body(...)):
    u = auth.require_user(request)
    brief = _clean_brief(body)
    import json as _json
    pid = db.ex("INSERT INTO projects(user_id, title, brief, created_at, updated_at) VALUES(?,?,?,?,?)",
                (u["id"], "โปรเจกต์ใหม่", _json.dumps(brief, ensure_ascii=False), db.now(), db.now()))
    pipeline.project_dir(pid)
    return {"id": pid}


@app.get("/api/projects/{pid}")
def api_project(pid: int, request: Request):
    u, p = own_project(request, pid)
    jobs = db.q("SELECT id, kind, cost, status, progress, error, refunded, created_at FROM jobs "
                "WHERE project_id=? ORDER BY id DESC LIMIT 20", (pid,))
    lib = db.q("SELECT id, title, mood, credit FROM music_tracks ORDER BY mood, title")
    story = p["story"]
    return {
        "id": pid, "title": p["title"], "brief": p["brief"], "story": story,
        "music_map": p["music_map"],
        "used_moods": pipeline.used_moods(story) if story else [],
        "expressions_needed": {c["id"]: pipeline.used_expressions(story, c["id"])
                               for c in story["characters"]} if story else {},
        "assets": asset_status(pid, story),
        "jobs": jobs, "music_library": lib,
        "credits": db.one("SELECT credits FROM users WHERE id=?", (u["id"],))["credits"],
    }


@app.delete("/api/projects/{pid}")
def api_delete_project(pid: int, request: Request):
    own_project(request, pid)
    if db.one("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)):
        raise HTTPException(400, "มีงานกำลังทำอยู่ รอให้เสร็จก่อน")
    import shutil
    db.ex("DELETE FROM projects WHERE id=?", (pid,))
    for t in db.q("SELECT id FROM topics WHERE project_id=?", (pid,)):
        community.delete_topic(t["id"])
    shutil.rmtree(config.PROJECTS_DIR / str(pid), ignore_errors=True)
    return {"ok": True}


@app.put("/api/projects/{pid}/story")
def api_save_story(pid: int, request: Request, body: dict = Body(...)):
    own_project(request, pid)
    try:
        story = pipeline.normalize_story(body.get("story"))
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(400, f"ข้อมูลเรื่องไม่ถูกต้อง: {e}")
    db.save_story(pid, story)
    pipeline.auto_assign_music(pid)
    return {"story": story}


@app.post("/api/projects/{pid}/quote")
def api_quote(pid: int, request: Request, body: dict = Body(...)):
    u, p = own_project(request, pid)
    kind = body.get("kind")
    payload = body.get("payload") or {}
    if kind != "story" and not p["story"]:
        raise HTTPException(400, "ต้องสร้างเรื่องก่อน")
    if kind == "story":
        payload = p["brief"]
    try:
        cost, payload, lines = pipeline.quote(kind, pid, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"cost": cost, "lines": lines, "balance": u["credits"], "enough": u["credits"] >= cost,
            "payload": payload}


JOB_LABELS = {"story": "เขียนเรื่อง", "rewrite_scene": "เขียนฉากใหม่", "images": "สร้างภาพ", "music": "สร้างเพลง",
              "video": "เรนเดอร์วิดีโอ"}


@app.post("/api/projects/{pid}/jobs")
async def api_start_job(pid: int, request: Request, body: dict = Body(...)):
    u, p = own_project(request, pid)
    kind = body.get("kind")
    payload = body.get("payload") or {}
    if kind != "story" and not p["story"]:
        raise HTTPException(400, "ต้องสร้างเรื่องก่อน")
    if kind == "story":
        payload = p["brief"]
    if kind == "rewrite_scene":
        payload = {"scene_id": str(payload.get("scene_id", "")),
                   "instruction": str(payload.get("instruction", ""))[:1000]}
    running = db.one("SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND status IN ('queued','running')", (u["id"],))
    if running["n"] >= 3:
        raise HTTPException(429, "มีงานทำอยู่หลายงานแล้ว รอให้เสร็จก่อนนะครับ")
    try:
        cost, payload, _ = pipeline.quote(kind, pid, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if kind == "images" and not payload["items"]:
        raise HTTPException(400, "ยังไม่ได้เลือกภาพที่จะสร้าง")
    if body.get("expected_cost") is not None and int(body["expected_cost"]) != cost:
        raise HTTPException(409, "ราคาเปลี่ยนไป กรุณากดประเมินใหม่")
    try:
        jid = pipeline.start_job(u["id"], pid, kind, payload, cost, JOB_LABELS.get(kind, kind))
    except db.InsufficientCredits:
        raise HTTPException(402, "เครดิตไม่พอ กรุณาเติมเครดิต")
    return {"job_id": jid}


@app.get("/api/projects/{pid}/jobs")
def api_jobs(pid: int, request: Request):
    own_project(request, pid)
    return {"jobs": db.q("SELECT id, kind, cost, status, progress, error, refunded, created_at FROM jobs "
                         "WHERE project_id=? ORDER BY id DESC LIMIT 20", (pid,))}


@app.put("/api/projects/{pid}/music")
def api_set_music(pid: int, request: Request, body: dict = Body(...)):
    _, p = own_project(request, pid)
    mood = body.get("mood")
    if mood not in config.MOODS:
        raise HTTPException(400, "mood ไม่ถูกต้อง")
    entry = body.get("entry")
    mm = p["music_map"]
    if not entry:
        mm[mood] = {"type": "none"}
    elif entry.get("type") == "lib" and db.one("SELECT id FROM music_tracks WHERE id=?", (entry.get("id"),)):
        mm[mood] = {"type": "lib", "id": int(entry["id"])}
    elif entry.get("type") == "gen":
        name = Path(str(entry.get("file"))).name
        if not (pipeline.project_dir(pid) / "music" / name).exists():
            raise HTTPException(400, "ไม่พบไฟล์เพลง")
        mm[mood] = {"type": "gen", "file": name}
    else:
        raise HTTPException(400, "ข้อมูลเพลงไม่ถูกต้อง")
    db.save_music_map(pid, mm)
    return {"music_map": mm}


@app.get("/api/projects/{pid}/generated-music")
def api_generated_music(pid: int, request: Request):
    own_project(request, pid)
    d = pipeline.project_dir(pid) / "music"
    return {"files": sorted(f.name for f in d.iterdir() if f.is_file())}


ALLOWED_IMG = {"image/png", "image/jpeg", "image/webp"}


@app.post("/api/projects/{pid}/upload-image")
async def api_upload_image(pid: int, request: Request, kind: str = Form(...), id: str = Form(...),
                           expr: str = Form("neutral"), file: UploadFile = File(...)):
    """Users can bring their own art — free of charge."""
    _, p = own_project(request, pid)
    story = p["story"]
    if not story:
        raise HTTPException(400, "ต้องสร้างเรื่องก่อน")
    raw = await file.read(15 * 1024 * 1024 + 1)
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 15MB")
    try:
        Image.open(io.BytesIO(raw)).verify()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "ไฟล์ภาพไม่ถูกต้อง")
    if kind == "bg" and any(b["id"] == id for b in story["backgrounds"]):
        pipeline.bg_path(pid, id).write_bytes(await asyncio.to_thread(imaging.process_background, raw))
    elif kind == "char" and any(c["id"] == id for c in story["characters"]) and expr in config.EXPRESSIONS:
        pipeline.char_path(pid, id, expr).write_bytes(await asyncio.to_thread(imaging.process_character, raw, False))
    else:
        raise HTTPException(400, "ข้อมูลไม่ถูกต้อง")
    return {"ok": True}


@app.delete("/api/projects/{pid}/image")
def api_delete_image(pid: int, request: Request, kind: str, id: str, expr: str = "neutral"):
    own_project(request, pid)
    f = pipeline.bg_path(pid, pipeline.slug(id)) if kind == "bg" else \
        pipeline.char_path(pid, pipeline.slug(id), expr if expr in config.EXPRESSIONS else "neutral")
    if f.exists():
        f.unlink()
    return {"ok": True}


@app.get("/api/projects/{pid}/gamedata")
def api_gamedata(pid: int, request: Request):
    _, p = own_project(request, pid)
    story = p["story"]
    if not story:
        raise HTTPException(400, "ยังไม่มีเรื่อง")
    assets = export.collect_assets(pid, story, p["music_map"])
    v = lambda f: int(f.stat().st_mtime)  # noqa: E731
    return {
        "format": "gamedevx-story/1",
        "story": story,
        "assets": {
            "chars": {k: f"/files/{pid}/chars/{f.name}?v={v(f)}" for k, f in assets["chars"].items()},
            "bgs": {k: f"/files/{pid}/bgs/{f.name}?v={v(f)}" for k, f in assets["bgs"].items()},
            "music": {k: _music_url(pid, p["music_map"].get(k)) for k in assets["music"]},
        },
    }


def _music_url(pid: int, entry: dict) -> str:
    if entry.get("type") == "lib":
        return f"/music/{entry['id']}"
    return f"/files/{pid}/music/{entry['file']}"


@app.get("/api/projects/{pid}/export/{kind}")
def api_export(pid: int, kind: str, request: Request):
    _, p = own_project(request, pid)
    if not p["story"]:
        raise HTTPException(400, "ยังไม่มีเรื่อง")
    fn = {"renpy": export.export_renpy, "web": export.export_web, "assets": export.export_assets}.get(kind)
    if not fn:
        raise HTTPException(404)
    data, name = fn(pid)
    ascii_name = re.sub(r"[^A-Za-z0-9_.-]", "", name).strip("_.") or f"gamedevx_{pid}_{kind}.zip"
    if not ascii_name.endswith(".zip") or len(ascii_name) < 12:
        ascii_name = f"gamedevx_{pid}_{kind}.zip"
    from urllib.parse import quote as _q
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{_q(name)}"})


# ============================================================ files
@app.get("/files/{pid}/{folder}/{name}")
def files(pid: int, folder: str, name: str, request: Request):
    own_project(request, pid)
    if folder not in ("chars", "bgs", "music", "refs", "video"):
        raise HTTPException(404)
    f = pipeline.project_dir(pid) / folder / Path(name).name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/music/{tid}")
def music_track(tid: int, request: Request):
    auth.require_user(request)
    t = db.one("SELECT filename FROM music_tracks WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    f = config.MUSIC_DIR / t["filename"]
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/music")
def api_music(request: Request):
    auth.require_user(request)
    return {"tracks": db.q("SELECT id, title, mood, credit FROM music_tracks ORDER BY mood, title")}


# ============================================================ admin API
@app.get("/api/admin/stats")
def admin_stats(request: Request):
    auth.require_admin(request)
    return {
        "users": db.one("SELECT COUNT(*) n FROM users")["n"],
        "projects": db.one("SELECT COUNT(*) n FROM projects")["n"],
        "jobs_today": db.one("SELECT COUNT(*) n FROM jobs WHERE created_at > ?", (time.time() - 86400,))["n"],
        "credits_added_30d": db.one("SELECT COALESCE(SUM(amount),0) n FROM credit_tx WHERE admin_id IS NOT NULL "
                                    "AND amount > 0 AND created_at > ?", (time.time() - 30 * 86400,))["n"],
        "credits_spent_30d": -db.one("SELECT COALESCE(SUM(amount),0) n FROM credit_tx WHERE amount < 0 "
                                     "AND created_at > ?", (time.time() - 30 * 86400,))["n"],
        "openrouter_key": bool(config.OPENROUTER_API_KEY),
        "fal_key": bool(config.FAL_KEY),
        "mock": config.MOCK_AI,
    }


@app.get("/api/admin/users")
def admin_users(request: Request, q: str = ""):
    auth.require_admin(request)
    like = f"%{q.strip().lower()}%"
    rows = db.q("SELECT id, email, name, is_admin, credits, disabled, created_at FROM users "
                "WHERE lower(email) LIKE ? OR lower(name) LIKE ? OR CAST(id AS TEXT)=? ORDER BY id DESC LIMIT 200",
                (like, like, q.strip()))
    return {"users": rows}


@app.post("/api/admin/users/{uid}/credits")
def admin_credits(uid: int, request: Request, body: dict = Body(...)):
    a = auth.require_admin(request)
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "จำนวนเครดิตไม่ถูกต้อง")
    if amount == 0:
        raise HTTPException(400, "จำนวนเครดิตต้องไม่เป็น 0")
    note = str(body.get("note") or "").strip()[:200]
    reason = ("เติมเครดิต" if amount > 0 else "หักเครดิต") + (f": {note}" if note else "")
    try:
        bal = db.change_credits(uid, amount, reason, admin_id=a["id"])
    except db.InsufficientCredits:
        raise HTTPException(400, "หักแล้วเครดิตจะติดลบ")
    except ValueError:
        raise HTTPException(404, "ไม่พบผู้ใช้")
    return {"balance": bal}


@app.post("/api/admin/users/{uid}/flags")
def admin_flags(uid: int, request: Request, body: dict = Body(...)):
    a = auth.require_admin(request)
    if uid == a["id"]:
        raise HTTPException(400, "แก้สิทธิ์ตัวเองไม่ได้")
    if "disabled" in body:
        db.ex("UPDATE users SET disabled=? WHERE id=?", (int(bool(body["disabled"])), uid))
    if "is_admin" in body:
        db.ex("UPDATE users SET is_admin=? WHERE id=?", (int(bool(body["is_admin"])), uid))
    return {"ok": True}


@app.post("/api/admin/users/{uid}/reset-password")
def admin_reset_pw(uid: int, request: Request):
    auth.require_admin(request)
    import secrets
    pw = secrets.token_urlsafe(8)
    db.ex("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_pw(pw), uid))
    return {"password": pw}


@app.get("/api/admin/tx")
def admin_tx(request: Request, uid: int | None = None):
    auth.require_admin(request)
    if uid:
        rows = db.q("SELECT t.*, u.email FROM credit_tx t JOIN users u ON u.id=t.user_id WHERE t.user_id=? "
                    "ORDER BY t.id DESC LIMIT 200", (uid,))
    else:
        rows = db.q("SELECT t.*, u.email FROM credit_tx t JOIN users u ON u.id=t.user_id "
                    "ORDER BY t.id DESC LIMIT 200")
    return {"items": rows}


@app.get("/api/admin/jobs")
def admin_jobs(request: Request):
    auth.require_admin(request)
    return {"jobs": db.q("SELECT j.id, j.kind, j.cost, j.status, j.progress, j.error, j.refunded, j.created_at, "
                         "j.project_id, u.email FROM jobs j JOIN users u ON u.id=j.user_id "
                         "ORDER BY j.id DESC LIMIT 100")}


@app.get("/api/admin/settings")
def admin_get_settings(request: Request):
    auth.require_admin(request)
    return {"settings": db.all_settings()}


@app.put("/api/admin/settings")
def admin_put_settings(request: Request, body: dict = Body(...)):
    auth.require_admin(request)
    for k, v in (body.get("settings") or {}).items():
        if k in config.DEFAULT_SETTINGS:
            v = str(v).strip() if k not in ("topup_packages", "announcement", "board_categories", "bad_words") else str(v)
            if k == "allow_guest_post":
                v = "1" if v in ("1", "true", "on", "yes") else "0"
            if k.startswith("cost_") or k in ("welcome_credits", "music_seconds", "board_per_page"):
                try:
                    v = str(max(0, int(float(v))))
                except ValueError:
                    raise HTTPException(400, f"{k} ต้องเป็นตัวเลข")
            if k == "image_provider" and v not in ("openrouter", "fal"):
                raise HTTPException(400, "image_provider ต้องเป็น openrouter หรือ fal")
            db.set_setting(k, v)
    return {"settings": db.all_settings()}


@app.post("/api/admin/test-openrouter")
async def admin_test_openrouter(request: Request):
    auth.require_admin(request)
    if not config.OPENROUTER_API_KEY:
        return {"ok": False, "message": "ยังไม่ได้ตั้ง OPENROUTER_API_KEY"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://openrouter.ai/api/v1/key",
                        headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"})
    if r.status_code != 200:
        return {"ok": False, "message": f"HTTP {r.status_code}: {r.text[:200]}"}
    return {"ok": True, "message": "เชื่อมต่อได้", "data": r.json().get("data")}


AUDIO_EXT = {".mp3", ".ogg", ".wav", ".opus", ".m4a", ".flac"}


@app.post("/api/admin/music")
async def admin_add_music(request: Request, title: str = Form(...), mood: str = Form(...),
                          credit: str = Form(""), file: UploadFile = File(...)):
    auth.require_admin(request)
    if mood not in config.MOODS:
        raise HTTPException(400, "mood ไม่ถูกต้อง")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(400, "รองรับไฟล์ " + ", ".join(sorted(AUDIO_EXT)))
    raw = await file.read(40 * 1024 * 1024 + 1)
    if len(raw) > 40 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 40MB")
    fname = f"{mood}_{uuid.uuid4().hex[:10]}{ext}"
    (config.MUSIC_DIR / fname).write_bytes(raw)
    tid = db.ex("INSERT INTO music_tracks(title, mood, filename, credit, created_at) VALUES(?,?,?,?,?)",
                (title.strip()[:100] or fname, mood, fname, credit.strip()[:200], db.now()))
    return {"id": tid}


@app.delete("/api/admin/music/{tid}")
def admin_del_music(tid: int, request: Request):
    auth.require_admin(request)
    t = db.one("SELECT filename FROM music_tracks WHERE id=?", (tid,))
    if t:
        (config.MUSIC_DIR / t["filename"]).unlink(missing_ok=True)
        db.ex("DELETE FROM music_tracks WHERE id=?", (tid,))
    return {"ok": True}



# ============================================================ reference images (photo / sketch)
@app.post("/api/projects/{pid}/upload-ref")
async def api_upload_ref(pid: int, request: Request, kind: str = Form(...), id: str = Form(...),
                         file: UploadFile = File(...)):
    _, p = own_project(request, pid)
    story = p["story"]
    if not story:
        raise HTTPException(400, "ต้องสร้างเรื่องก่อน")
    items = story["characters"] if kind == "char" else story["backgrounds"] if kind == "bg" else []
    if not any(x["id"] == id for x in items):
        raise HTTPException(400, "ข้อมูลไม่ถูกต้อง")
    raw = await file.read(15 * 1024 * 1024 + 1)
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 15MB")
    try:
        out = await asyncio.to_thread(imaging.normalize_reference, raw)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "ไฟล์ภาพไม่ถูกต้อง")
    pipeline.ref_path(pid, kind, id).write_bytes(out)
    return {"ok": True}


@app.delete("/api/projects/{pid}/ref")
def api_delete_ref(pid: int, request: Request, kind: str, id: str):
    own_project(request, pid)
    pipeline.ref_path(pid, "char" if kind == "char" else "bg", pipeline.slug(id)).unlink(missing_ok=True)
    return {"ok": True}


# ============================================================ videos
@app.get("/api/projects/{pid}/videos")
def api_videos(pid: int, request: Request):
    own_project(request, pid)
    d = pipeline.project_dir(pid) / "video"
    vids = sorted(d.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {"videos": [{"name": f.name, "size": f.stat().st_size, "created_at": f.stat().st_mtime,
                        "url": f"/files/{pid}/video/{f.name}"} for f in vids]}


# community board, banners/ads and public play live in community.py
app.include_router(community.router)


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/") or request.url.path.startswith("/files/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 404:
        return HTMLResponse("<h1>404</h1><p><a href='/'>กลับหน้าแรก</a></p>", status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
