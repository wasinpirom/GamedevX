"""The AI agent pipeline: story planning, asset generation, music, credit quotes and background jobs."""
import asyncio
import json
import re
import time
import traceback
from pathlib import Path

from . import config, db, imaging, providers

# ======================================================================= paths
def project_dir(pid: int) -> Path:
    d = config.PROJECTS_DIR / str(pid)
    (d / "chars").mkdir(parents=True, exist_ok=True)
    (d / "bgs").mkdir(parents=True, exist_ok=True)
    (d / "music").mkdir(parents=True, exist_ok=True)
    (d / "refs").mkdir(parents=True, exist_ok=True)
    (d / "video").mkdir(parents=True, exist_ok=True)
    return d


def ref_path(pid: int, kind: str, item_id: str) -> Path:
    """User-supplied reference image (photo or sketch) for a character or background."""
    return project_dir(pid) / "refs" / f"{'char' if kind == 'char' else 'bg'}_{item_id}.png"


def char_path(pid: int, cid: str, expr: str) -> Path:
    return project_dir(pid) / "chars" / f"{cid}_{expr}.png"


def bg_path(pid: int, bid: str) -> Path:
    return project_dir(pid) / "bgs" / f"{bid}.jpg"


# ======================================================================= story schema
LINE_TYPES = ("narr", "say", "choice", "goto", "end")
RESERVED = {"start", "main_menu", "quit", "after_load", "splashscreen", "before_main_menu", "after_warp",
            "narrator", "renpy", "config", "store", "gui", "style", "im", "ui", "layout", "preferences",
            "persistent", "build", "define", "default", "image", "label", "scene", "show", "hide", "menu",
            "jump", "call", "return", "if", "else", "elif", "while", "pass", "python", "init", "screen",
            "transform", "bg", "e", "extend", "nvl", "adv", "centered", "vcentered", "left", "right", "center"}


def slug(s: str, prefix: str = "x") -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", str(s or "").lower()).strip("_")
    if not s or not s[0].isalpha() or s in RESERVED:
        s = f"{prefix}_{s}" if s else prefix
    return s[:40]


def _unique(base: str, used: set) -> str:
    s, i = base, 2
    while s in used:
        s = f"{base}_{i}"
        i += 1
    used.add(s)
    return s


PALETTE = ["#e8577e", "#4a90e2", "#f5a623", "#7ed321", "#9b59b6", "#16a085", "#e67e22"]


def normalize_story(raw: dict) -> dict:
    """Validate and repair a story so the player and the Ren'Py exporter can always run it."""
    if not isinstance(raw, dict):
        raise ValueError("story must be an object")
    story = {
        "title": str(raw.get("title") or "เรื่องไม่มีชื่อ")[:120],
        "synopsis": str(raw.get("synopsis") or "")[:2000],
        "art_style": raw.get("art_style") if raw.get("art_style") in config.ART_STYLES else "anime",
        "characters": [],
        "backgrounds": [],
        "scenes": [],
    }
    # characters
    cmap, used = {}, set()
    for i, c in enumerate(raw.get("characters") or []):
        if not isinstance(c, dict):
            continue
        orig = str(c.get("id") or c.get("name") or f"c{i}")
        cid = _unique(slug(orig, "ch"), used)
        cmap[orig] = cid
        cmap[cid] = cid
        if c.get("name"):
            cmap[str(c["name"])] = cid
        color = str(c.get("color") or PALETTE[i % len(PALETTE)])
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = PALETTE[i % len(PALETTE)]
        story["characters"].append({
            "id": cid,
            "name": str(c.get("name") or cid)[:40],
            "color": color,
            "look": str(c.get("look") or c.get("description") or "")[:600],
            "personality": str(c.get("personality") or "")[:300],
        })
    # backgrounds
    bmap, used = {}, set()
    for i, b in enumerate(raw.get("backgrounds") or []):
        if not isinstance(b, dict):
            continue
        orig = str(b.get("id") or b.get("name") or f"b{i}")
        bid = _unique(slug(orig, "bg"), used)
        bmap[orig] = bid
        bmap[bid] = bid
        story["backgrounds"].append({
            "id": bid,
            "name": str(b.get("name") or bid)[:60],
            "look": str(b.get("look") or b.get("description") or "")[:600],
        })
    # scenes (first pass: ids)
    smap, used = {}, set()
    raw_scenes = [s for s in (raw.get("scenes") or []) if isinstance(s, dict)]
    for i, s in enumerate(raw_scenes):
        orig = str(s.get("id") or f"s{i + 1}")
        smap[orig] = _unique(slug(orig, "sc"), used)
    first_bg = story["backgrounds"][0]["id"] if story["backgrounds"] else None
    for i, s in enumerate(raw_scenes):
        sid = smap[str(s.get("id") or f"s{i + 1}")]
        bg = bmap.get(str(s.get("bg") or s.get("background") or ""), first_bg)
        mood = s.get("mood") if s.get("mood") in config.MOODS else "calm"
        lines = []
        for ln in s.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            t = ln.get("t") or ln.get("type")
            if t == "say":
                who = cmap.get(str(ln.get("who") or ""))
                text = str(ln.get("text") or "").strip()
                if not text:
                    continue
                if not who:
                    lines.append({"t": "narr", "text": text})
                    continue
                expr = ln.get("expr") if ln.get("expr") in config.EXPRESSIONS else "neutral"
                lines.append({"t": "say", "who": who, "expr": expr, "text": text})
            elif t == "narr":
                text = str(ln.get("text") or "").strip()
                if text:
                    lines.append({"t": "narr", "text": text})
            elif t == "choice":
                opts = []
                for o in ln.get("options") or []:
                    if isinstance(o, dict) and str(o.get("text") or "").strip():
                        goto = smap.get(str(o.get("goto") or ""))
                        opts.append({"text": str(o["text"]).strip(), "goto": goto})
                if opts:
                    lines.append({"t": "choice", "options": opts})
            elif t == "goto":
                goto = smap.get(str(ln.get("scene") or ln.get("goto") or ""))
                if goto:
                    lines.append({"t": "goto", "scene": goto})
            elif t == "end":
                lines.append({"t": "end"})
        story["scenes"].append({
            "id": sid, "title": str(s.get("title") or sid)[:80],
            "bg": bg, "mood": mood, "lines": lines,
        })
    if not story["scenes"]:
        raise ValueError("เรื่องต้องมีอย่างน้อย 1 ฉาก")
    # choices without valid target fall through to next scene
    ids = [s["id"] for s in story["scenes"]]
    for idx, s in enumerate(story["scenes"]):
        nxt = ids[idx + 1] if idx + 1 < len(ids) else None
        for ln in s["lines"]:
            if ln["t"] == "choice":
                for o in ln["options"]:
                    if not o["goto"]:
                        o["goto"] = nxt
    return story


def used_expressions(story: dict, cid: str) -> list[str]:
    used = {"neutral"}
    for s in story.get("scenes", []):
        for ln in s["lines"]:
            if ln["t"] == "say" and ln["who"] == cid:
                used.add(ln["expr"])
    return [e for e in config.EXPRESSIONS if e in used]


def used_moods(story: dict) -> list[str]:
    seen = []
    for s in story.get("scenes", []):
        if s["mood"] not in seen:
            seen.append(s["mood"])
    return seen


# ======================================================================= prompts
STORY_SYSTEM = """You are GamedevX, an expert visual novel writer and game designer.
You write branching visual novel scripts that will be turned into a playable Ren'Py game.
Reply with ONE JSON object only, no markdown, matching this schema exactly:
{
 "title": string,
 "synopsis": string (2-4 sentences),
 "characters": [{"id": "lowercase_ascii_id", "name": "display name", "color": "#RRGGBB",
                 "look": "ENGLISH visual description for an illustrator: age, hair, eyes, outfit, colors (no art style words)",
                 "personality": "short"}],
 "backgrounds": [{"id": "lowercase_ascii_id", "name": "display name", "look": "ENGLISH description of the place, time of day, lighting (no people)"}],
 "scenes": [{"id": "s1", "title": "short", "bg": "<background id>", "mood": "<one of MOODS>",
             "lines": [ LINE, ... ]}]
}
LINE is one of:
 {"t": "narr", "text": "narration"}
 {"t": "say", "who": "<character id>", "expr": "<one of EXPRESSIONS>", "text": "dialogue"}
 {"t": "choice", "options": [{"text": "choice label", "goto": "<scene id>"}, ...]}   (2-3 options, only as the LAST line of a scene)
 {"t": "goto", "scene": "<scene id>"}   (only as the last line)
 {"t": "end"}   (ending; last line of an ending scene)
Rules:
- A scene with no choice/goto/end at the end continues to the next scene in the list.
- Every branch must reach an {"t":"end"} line. Endings should be placed after the main path scenes.
- Each scene has 6-14 lines. Keep each dialogue line under 140 characters.
- MOODS: """ + ", ".join(config.MOODS) + """
- EXPRESSIONS: """ + ", ".join(config.EXPRESSIONS) + """
- Use only ids you defined. Reuse backgrounds across scenes when it makes sense.
"""


def story_user_prompt(brief: dict) -> str:
    lang = "Thai (ภาษาไทย)" if brief.get("language", "th") == "th" else "English"
    branching = ("Include 1-2 meaningful choices that lead to at least 2 different endings."
                 if brief.get("branching", True) else "Linear story, no choices, one ending.")
    return (
        f"Story idea from the user: {brief.get('idea', '')}\n"
        f"Genre: {brief.get('genre', '')}\n"
        f"Write all titles, names, narration, dialogue and choices in {lang}. "
        f"'look' fields must be in English.\n"
        f"Number of scenes (including endings): about {brief.get('scenes', 5)}.\n"
        f"Number of characters: {brief.get('characters', 2)} (the player is not a drawn character; "
        f"refer to the player in narration).\n"
        f"Number of distinct backgrounds: at most {max(2, int(brief.get('scenes', 5)) // 2 + 1)}.\n"
        f"{branching}"
    )


REWRITE_SYSTEM = """You are GamedevX, rewriting ONE scene of a visual novel script.
Reply with ONE JSON object only: {"scene": SCENE} where SCENE has the same schema as before:
{"id": "<keep the same id>", "title": string, "bg": "<existing background id>", "mood": "<mood>", "lines": [LINE...]}
LINE types: narr, say (who/expr/text), choice (options text/goto), goto (scene), end.
Use only existing character ids, background ids and scene ids. Keep the language of the story.
MOODS: """ + ", ".join(config.MOODS) + "\nEXPRESSIONS: " + ", ".join(config.EXPRESSIONS)


def char_prompt(story: dict, ch: dict, expr: str, with_ref: bool, user_ref: bool = False) -> str:
    style = config.ART_STYLES[story.get("art_style", "anime")]
    expr_en = {"neutral": "calm neutral", "happy": "happy smiling", "sad": "sad", "angry": "angry",
               "surprised": "surprised", "shy": "shy blushing"}[expr]
    if user_ref:
        return (f"Create a full body character sprite for a visual novel, {style}, based on the person or character "
                f"in the reference image (a photo or a sketch). Keep their recognizable face shape, facial features, "
                f"hairstyle and hair color so they clearly look like the reference, but redraw everything in the "
                f"art style. Character notes: {ch['look']}. Standing, facing the viewer, {expr_en} expression. "
                f"Whole body visible from head to feet, centered. Plain pure white background, no text, no logo, "
                f"no shadow, no other people.")
    if with_ref:
        return (f"Same character as the reference image: identical face, hair, outfit, colors, proportions and "
                f"art style. Change only the facial expression and pose slightly to look {expr_en}. "
                f"Full body standing, centered, plain pure white background, no text, no shadow.")
    return (f"Full body character sprite for a visual novel, {style}. {ch['look']}. "
            f"Standing, facing the viewer, {expr_en} expression. Whole body visible from head to feet, centered. "
            f"Plain pure white background, no text, no logo, no shadow, no other people.")


def bg_prompt(story: dict, bg: dict, user_ref: bool = False) -> str:
    style = config.ART_STYLES[story.get("art_style", "anime")]
    if user_ref:
        return (f"Redraw the place in the reference image (a photo or a sketch) as a visual novel background "
                f"illustration, {style}. Keep the layout and main landmarks of the reference. {bg['look']}. "
                f"Wide landscape composition, no people, no characters, no text, no logo.")
    return (f"Visual novel background illustration, {style}. {bg['look']}. "
            f"Wide landscape composition, detailed environment, no people, no characters, no text, no logo.")


def music_prompt(story: dict, mood: str) -> str:
    mood_en = {"happy": "cheerful upbeat", "calm": "calm relaxing", "romantic": "romantic gentle piano and strings",
               "sad": "sad emotional piano", "tense": "tense suspenseful", "horror": "dark eerie horror ambient",
               "funny": "playful comedic", "mystery": "mysterious curious", "epic": "epic orchestral battle",
               "ending": "warm emotional finale"}[mood]
    title = story.get("title", "") if story else ""
    return (f"{mood_en} instrumental background music loop for a visual novel game"
            f"{' titled ' + title if title else ''}, no vocals, seamless loop, high quality")


# ======================================================================= mock story
def mock_story(brief: dict) -> dict:
    n = max(3, min(int(brief.get("scenes", 4)), 8))
    scenes = [
        {"id": "s1", "title": "จุดเริ่มต้น", "bg": "school", "mood": "happy", "lines": [
            {"t": "narr", "text": "เช้าวันแรกของเทอมใหม่ ลมเย็นพัดผ่านประตูโรงเรียน"},
            {"t": "say", "who": "mali", "expr": "happy", "text": "สวัสดี! เธอคือนักเรียนใหม่ใช่ไหม"},
            {"t": "say", "who": "mali", "expr": "shy", "text": "เราชื่อมะลินะ ยินดีที่ได้รู้จัก"},
            {"t": "say", "who": "korn", "expr": "neutral", "text": "มะลิ อย่าเพิ่งทำให้เขาตกใจสิ"},
            {"t": "choice", "options": [{"text": "ไปห้องสมุดกับมะลิ", "goto": "s2"},
                                        {"text": "ไปดาดฟ้ากับกร", "goto": "s3"}]}]},
        {"id": "s2", "title": "ห้องสมุดเก่า", "bg": "library", "mood": "mystery", "lines": [
            {"t": "narr", "text": "ห้องสมุดเงียบจนได้ยินเสียงหน้ากระดาษพลิก"},
            {"t": "say", "who": "mali", "expr": "surprised", "text": "เดี๋ยวนะ หนังสือเล่มนี้เรืองแสงได้!"},
            {"t": "goto", "scene": "s4"}]},
        {"id": "s3", "title": "บนดาดฟ้า", "bg": "school", "mood": "calm", "lines": [
            {"t": "say", "who": "korn", "expr": "sad", "text": "จริง ๆ แล้วเราไม่ค่อยมีเพื่อนหรอก"},
            {"t": "say", "who": "korn", "expr": "happy", "text": "แต่วันนี้ดูเหมือนจะไม่เหงาแล้ว"},
            {"t": "goto", "scene": "s5"}]},
        {"id": "s4", "title": "ตอนจบ: ความลับของหนังสือ", "bg": "library", "mood": "ending", "lines": [
            {"t": "narr", "text": "การผจญภัยครั้งใหม่ของเธอกับมะลิเพิ่งเริ่มต้นขึ้น"}, {"t": "end"}]},
        {"id": "s5", "title": "ตอนจบ: เพื่อนแท้", "bg": "school", "mood": "ending", "lines": [
            {"t": "narr", "text": "มิตรภาพเล็ก ๆ บนดาดฟ้า กลายเป็นความทรงจำที่ไม่มีวันลืม"}, {"t": "end"}]},
    ]
    return {
        "title": "ห้องสมุดเรืองแสง (ตัวอย่าง MOCK)",
        "synopsis": f"เรื่องตัวอย่างจากโหมดทดสอบ (MOCK_AI) ไอเดีย: {brief.get('idea', '')[:200]}",
        "art_style": brief.get("art_style", "anime"),
        "characters": [
            {"id": "mali", "name": "มะลิ", "color": "#e8577e", "look": "teen girl, long black hair, school uniform"},
            {"id": "korn", "name": "กร", "color": "#4a90e2", "look": "teen boy, short brown hair, glasses"}],
        "backgrounds": [
            {"id": "school", "name": "หน้าโรงเรียน", "look": "Thai school gate in the morning"},
            {"id": "library", "name": "ห้องสมุด", "look": "old library with tall wooden shelves"}],
        "scenes": scenes[:max(n, 5)],
    }


# ======================================================================= quotes
def expand_image_items(pid: int, story: dict, items: list[dict]) -> list[dict]:
    """Validate requested image items; auto-add the neutral pose needed as reference."""
    chars = {c["id"]: c for c in story["characters"]}
    bgs = {b["id"]: b for b in story["backgrounds"]}
    out, seen = [], set()
    for it in items:
        kind = it.get("kind")
        if kind == "char" and it.get("id") in chars and it.get("expr") in config.EXPRESSIONS:
            cid, expr = it["id"], it["expr"]
            if expr != "neutral" and not char_path(pid, cid, "neutral").exists():
                key = ("char", cid, "neutral")
                if key not in seen:
                    seen.add(key)
                    out.append({"kind": "char", "id": cid, "expr": "neutral"})
            key = ("char", cid, expr)
            if key not in seen:
                seen.add(key)
                out.append({"kind": "char", "id": cid, "expr": expr})
        elif kind == "bg" and it.get("id") in bgs:
            key = ("bg", it["id"])
            if key not in seen:
                seen.add(key)
                out.append({"kind": "bg", "id": it["id"]})
    # neutral poses first so they can be used as references
    out.sort(key=lambda x: (x["kind"] != "char", x.get("expr") != "neutral"))
    return out[:60]


def quote(kind: str, pid: int, payload: dict) -> tuple[int, dict, list[str]]:
    """Returns (cost, normalized payload, human readable lines)."""
    if kind == "story":
        c = db.get_int_setting("cost_story")
        return c, payload, [f"เขียนเรื่องและวางโครงเกมทั้งหมด = {c} เครดิต"]
    if kind == "rewrite_scene":
        c = db.get_int_setting("cost_scene_rewrite")
        return c, payload, [f"ให้ AI เขียนฉากใหม่ 1 ฉาก = {c} เครดิต"]
    if kind == "images":
        p = db.load_project(pid)
        items = expand_image_items(pid, p["story"], payload.get("items") or [])
        cc, cb = db.get_int_setting("cost_character_image"), db.get_int_setting("cost_background_image")
        n_c = sum(1 for i in items if i["kind"] == "char")
        n_b = sum(1 for i in items if i["kind"] == "bg")
        lines = []
        if n_c:
            lines.append(f"ภาพตัวละคร {n_c} ภาพ × {cc} = {n_c * cc} เครดิต")
        if n_b:
            lines.append(f"ภาพฉากหลัง {n_b} ภาพ × {cb} = {n_b * cb} เครดิต")
        return n_c * cc + n_b * cb, {"items": items}, lines
    if kind == "music":
        c = db.get_int_setting("cost_music")
        mood = payload.get("mood")
        if mood not in config.MOODS:
            raise ValueError("mood ไม่ถูกต้อง")
        return c, {"mood": mood}, [f"สร้างเพลงใหม่อารมณ์ \"{config.MOODS[mood]}\" = {c} เครดิต"]
    if kind == "video":
        c = db.get_int_setting("cost_video")
        route = payload.get("route") or {}
        route = {str(k): int(v) for k, v in route.items() if str(v).lstrip("-").isdigit()}
        return c, {"route": route}, [f"เรนเดอร์วิดีโอ MP4 (1280x720) สำหรับ YouTube = {c} เครดิต"]
    raise ValueError("unknown job kind")


# ======================================================================= job runner
_api_sem: asyncio.Semaphore | None = None
_tasks: set = set()


def _sem() -> asyncio.Semaphore:
    global _api_sem
    if _api_sem is None:
        _api_sem = asyncio.Semaphore(4)
    return _api_sem


def start_job(user_id: int, pid: int, kind: str, payload: dict, cost: int, label: str) -> int:
    """Deducts credits first (raises InsufficientCredits) then schedules the job."""
    jid = db.ex("INSERT INTO jobs(user_id, project_id, kind, payload, cost, status, created_at, updated_at) "
                "VALUES(?,?,?,?,?,'queued',?,?)",
                (user_id, pid, kind, json.dumps(payload, ensure_ascii=False), cost, db.now(), db.now()))
    if cost > 0:
        try:
            db.change_credits(user_id, -cost, f"{label} (งาน #{jid}, โปรเจกต์ #{pid})")
        except Exception:
            db.ex("DELETE FROM jobs WHERE id=?", (jid,))
            raise
    t = asyncio.get_running_loop().create_task(_run(jid))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return jid


def _refund(job: dict, amount: int, why: str) -> None:
    if amount <= 0:
        return
    db.change_credits(job["user_id"], amount, f"คืนเครดิต: {why} (งาน #{job['id']})")
    db.job_update(job["id"], refunded=amount)


async def _run(jid: int) -> None:
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    payload = json.loads(job["payload"])
    db.job_update(jid, status="running")
    try:
        if job["kind"] == "story":
            await _job_story(job, payload)
        elif job["kind"] == "rewrite_scene":
            await _job_rewrite(job, payload)
        elif job["kind"] == "images":
            await _job_images(job, payload)
            return  # handles its own status/refunds
        elif job["kind"] == "music":
            await _job_music(job, payload)
        elif job["kind"] == "video":
            await _job_video(job, payload)
        db.job_update(jid, status="done", progress="เสร็จแล้ว")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        db.job_update(jid, status="failed", error=str(e)[:500])
        _refund(job, job["cost"], "งานล้มเหลว")


async def _job_story(job: dict, brief: dict) -> None:
    db.job_update(job["id"], progress="AI กำลังเขียนเรื่องและวางโครงเกม...")
    if config.MOCK_AI:
        await asyncio.sleep(1)
        raw = mock_story(brief)
    else:
        async with _sem():
            raw = await providers.llm_json(STORY_SYSTEM, story_user_prompt(brief))
    raw["art_style"] = brief.get("art_style", "anime")
    story = normalize_story(raw)
    db.save_story(job["project_id"], story)
    auto_assign_music(job["project_id"])


async def _job_rewrite(job: dict, payload: dict) -> None:
    p = db.load_project(job["project_id"])
    story = p["story"]
    scene = next((s for s in story["scenes"] if s["id"] == payload.get("scene_id")), None)
    if not scene:
        raise ValueError("ไม่พบฉาก")
    db.job_update(job["id"], progress="AI กำลังเขียนฉากใหม่...")
    if config.MOCK_AI:
        await asyncio.sleep(1)
        new_scene = dict(scene)
        new_scene["lines"] = [{"t": "narr", "text": f"(MOCK) เขียนใหม่ตามคำสั่ง: {payload.get('instruction', '')}"}] \
            + scene["lines"]
    else:
        ctx = {
            "title": story["title"], "synopsis": story["synopsis"],
            "characters": [{k: c[k] for k in ("id", "name", "personality")} for c in story["characters"]],
            "backgrounds": [{k: b[k] for k in ("id", "name")} for b in story["backgrounds"]],
            "scene_ids": [s["id"] + ": " + s["title"] for s in story["scenes"]],
        }
        user = (f"Story context: {json.dumps(ctx, ensure_ascii=False)}\n\n"
                f"Current scene: {json.dumps(scene, ensure_ascii=False)}\n\n"
                f"Instruction from the user: {payload.get('instruction') or 'make it better and more vivid'}")
        async with _sem():
            res = await providers.llm_json(REWRITE_SYSTEM, user, max_tokens=4000)
        new_scene = res.get("scene") or res
        new_scene["id"] = scene["id"]
    # re-normalize the whole story with the replaced scene (re-read in case user edited meanwhile)
    p = db.load_project(job["project_id"])
    story = p["story"]
    story["scenes"] = [new_scene if s["id"] == scene["id"] else s for s in story["scenes"]]
    db.save_story(job["project_id"], normalize_story(story))
    auto_assign_music(job["project_id"])


async def _gen_one_image(pid: int, story: dict, it: dict) -> None:
    if it["kind"] == "bg":
        bg = next(b for b in story["backgrounds"] if b["id"] == it["id"])
        rp = ref_path(pid, "bg", bg["id"])
        uref = rp.read_bytes() if rp.exists() else None
        async with _sem():
            raw = await providers.generate_image(bg_prompt(story, bg, uref is not None), "16:9", uref)
        out = await asyncio.to_thread(imaging.process_background, raw)
        bg_path(pid, bg["id"]).write_bytes(out)
        return
    ch = next(c for c in story["characters"] if c["id"] == it["id"])
    ref, user_ref = None, False
    if it["expr"] != "neutral":
        np_ = char_path(pid, ch["id"], "neutral")
        if np_.exists():
            ref = np_.read_bytes()
    if ref is None:
        rp = ref_path(pid, "char", ch["id"])
        if rp.exists():  # the user's photo / sketch drives the main pose
            ref, user_ref = rp.read_bytes(), True
    prompt = char_prompt(story, ch, it["expr"], ref is not None and not user_ref, user_ref)
    async with _sem():
        raw = await providers.generate_image(prompt, "2:3", ref)
        cut = None
        try:
            cut = await providers.remove_background(raw)
        except Exception:  # noqa: BLE001 - fall back to local removal
            traceback.print_exc()
    final = await asyncio.to_thread(imaging.process_character, cut or raw, cut is not None)
    char_path(pid, ch["id"], it["expr"]).write_bytes(final)


async def _job_images(job: dict, payload: dict) -> None:
    pid = job["project_id"]
    story = db.load_project(pid)["story"]
    items = payload["items"]
    cc, cb = db.get_int_setting("cost_character_image"), db.get_int_setting("cost_background_image")
    # use the per-item prices that were charged (derive from total to stay exact)
    charged_c = sum(1 for i in items if i["kind"] == "char")
    charged_b = len(items) - charged_c
    total_listed = charged_c * cc + charged_b * cb
    ratio = job["cost"] / total_listed if total_listed else 0
    done, failed, errors = 0, [], []
    # 1) neutral poses + backgrounds in parallel, 2) other expressions (need the neutral reference)
    first = [i for i in items if i["kind"] == "bg" or i.get("expr") == "neutral"]
    second = [i for i in items if i not in first]
    for batch in (first, second):
        async def run(it):
            nonlocal done
            try:
                await _gen_one_image(pid, story, it)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                failed.append(it)
                errors.append(str(e)[:200])
            done += 1
            db.job_update(job["id"], progress=f"สร้างภาพแล้ว {done}/{len(items)}")
        await asyncio.gather(*(run(i) for i in batch))
    refund = round(sum((cc if i["kind"] == "char" else cb) for i in failed) * ratio)
    if failed and len(failed) == len(items):
        db.job_update(job["id"], status="failed", error=errors[0] if errors else "ล้มเหลว")
        _refund(job, job["cost"], "สร้างภาพไม่สำเร็จ")
        return
    if failed:
        _refund(job, refund, f"ภาพที่สร้างไม่สำเร็จ {len(failed)} ภาพ")
        db.job_update(job["id"], status="done",
                      progress=f"เสร็จ {len(items) - len(failed)}/{len(items)} ภาพ (คืนเครดิตภาพที่ล้มเหลวแล้ว)",
                      error=errors[0] if errors else "")
    else:
        db.job_update(job["id"], status="done", progress=f"สร้างภาพครบ {len(items)} ภาพ")


async def _job_music(job: dict, payload: dict) -> None:
    pid = job["project_id"]
    p = db.load_project(pid)
    mood = payload["mood"]
    db.job_update(job["id"], progress="กำลังแต่งเพลงด้วย AI (อาจใช้เวลา 1-3 นาที)...")
    async with _sem():
        audio, ext = await providers.generate_music(music_prompt(p["story"], mood),
                                                    db.get_int_setting("music_seconds") or 60)
    fname = f"gen_{mood}_{int(time.time())}.{ext}"
    (project_dir(pid) / "music" / fname).write_bytes(audio)
    p = db.load_project(pid)
    mm = p["music_map"]
    mm[mood] = {"type": "gen", "file": fname}
    db.save_music_map(pid, mm)


# ======================================================================= music library
def auto_assign_music(pid: int) -> None:
    """Fill in a library track for every mood the story uses (keeps user's choices)."""
    p = db.load_project(pid)
    if not p or not p["story"]:
        return
    mm = p["music_map"]
    changed = False
    for mood in used_moods(p["story"]):
        if mood in mm:
            continue
        t = db.one("SELECT id FROM music_tracks WHERE mood=? ORDER BY RANDOM() LIMIT 1", (mood,))
        if not t and mood == "ending":
            t = db.one("SELECT id FROM music_tracks WHERE mood IN ('calm','romantic','happy') "
                       "ORDER BY RANDOM() LIMIT 1")
        if t:
            mm[mood] = {"type": "lib", "id": t["id"]}
            changed = True
    if changed:
        db.save_music_map(pid, mm)


def music_file(pid: int, entry: dict | None) -> Path | None:
    if not entry:
        return None
    if entry.get("type") == "lib":
        t = db.one("SELECT filename FROM music_tracks WHERE id=?", (entry.get("id"),))
        if t:
            f = config.MUSIC_DIR / t["filename"]
            return f if f.exists() else None
        return None
    if entry.get("type") == "gen":
        f = project_dir(pid) / "music" / Path(str(entry.get("file"))).name
        return f if f.exists() else None
    return None


def recover_interrupted_jobs() -> None:
    """On startup: jobs that were running when the server stopped get refunded."""
    for j in db.q("SELECT * FROM jobs WHERE status IN ('queued','running')"):
        db.job_update(j["id"], status="failed", error="เซิร์ฟเวอร์รีสตาร์ตระหว่างทำงาน")
        if not j["refunded"]:
            _refund(j, j["cost"], "เซิร์ฟเวอร์รีสตาร์ต")


async def _job_video(job: dict, payload: dict) -> None:
    from . import export, video  # local import (avoids circular import)
    pid = job["project_id"]
    p = db.load_project(pid)
    out = project_dir(pid) / "video" / f"{export.safe_filename(p['story']['title'])}_{int(time.time())}.mp4"

    def progress(msg: str) -> None:
        db.job_update(job["id"], progress=msg)

    await asyncio.to_thread(video.render_video, pid, p["story"], p["music_map"], payload.get("route") or {},
                            out, progress)
    # keep only the 3 newest videos per project
    vids = sorted((project_dir(pid) / "video").glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in vids[3:]:
        old.unlink(missing_ok=True)
    db.job_update(job["id"], progress=f"วิดีโอพร้อมแล้ว: {out.name}")
