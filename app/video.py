"""Render a visual novel route to an MP4 video (1280x720) for YouTube.

Frames are drawn with Pillow (Thai text shaped with raqm when available),
then assembled with ffmpeg together with the scene music.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, features

from . import config, export

W, H = 1280, 720
FONT_DIR = Path(__file__).parent / "renpy_template" / "fonts"
_LAYOUT = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC

try:
    from pythainlp.tokenize import word_tokenize as _tok
except Exception:  # pragma: no cover
    _tok = None


class VideoError(Exception):
    pass


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / ("thai-bold.otf" if bold else "thai.otf")), size, layout_engine=_LAYOUT)


def _tokens(text: str) -> list[str]:
    if _tok:
        try:
            return _tok(text, engine="newmm", keep_whitespace=True)
        except Exception:  # noqa: BLE001
            pass
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch == " ":
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for t in _tokens(para):
            if font.getlength(cur + t) <= width or not cur:
                cur += t
            else:
                lines.append(cur.rstrip())
                cur = t.lstrip()
        lines.append(cur.rstrip())
    return lines


def _hex(c: str, a: int = 255) -> tuple:
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


# ---------------------------------------------------------------- route walking
def walk(story: dict, route: dict) -> list[tuple]:
    """Follow the story from the start; at each choice take route[scene_id] (default first option)."""
    ids = [s["id"] for s in story["scenes"]]
    by_id = {s["id"]: s for s in story["scenes"]}
    events, visits, steps = [], {}, 0
    cur = ids[0] if ids else None
    while cur and cur in by_id and steps < 600:
        visits[cur] = visits.get(cur, 0) + 1
        if visits[cur] > 2:
            break
        sc = by_id[cur]
        events.append(("scene", sc))
        i = ids.index(cur)
        nxt = ids[i + 1] if i + 1 < len(ids) else None
        for ln in sc["lines"]:
            steps += 1
            if ln["t"] in ("narr", "say"):
                events.append(("line", sc, ln))
            elif ln["t"] == "choice":
                k = max(0, min(int(route.get(cur, 0)), len(ln["options"]) - 1))
                events.append(("choice", sc, ln, k))
                nxt = ln["options"][k].get("goto")
                break
            elif ln["t"] == "goto":
                nxt = ln["scene"]
                break
            elif ln["t"] == "end":
                nxt = None
                break
        cur = nxt
    return events


# ---------------------------------------------------------------- drawing
class Painter:
    def __init__(self, story: dict, assets: dict):
        self.story = story
        self.assets = assets
        self.chars = {c["id"]: c for c in story["characters"]}
        self.f_text = _font(30)
        self.f_name = _font(28, True)
        self.f_title = _font(64, True)
        self.f_sub = _font(28)
        self.f_mark = _font(20, True)
        self._bg_cache: dict = {}
        self._ch_cache: dict = {}
        host = urlparse(config.SITE_URL).netloc or "GamedevX.com"
        self.mark = host.replace("www.", "")

    def bg(self, bid: str | None) -> Image.Image:
        if bid in self._bg_cache:
            return self._bg_cache[bid]
        p = self.assets["bgs"].get(bid) if bid else None
        if p:
            im = Image.open(p).convert("RGB")
            scale = max(W / im.width, H / im.height)
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
            l, t = (im.width - W) // 2, (im.height - H) // 2
            im = im.crop((l, t, l + W, t + H))
        else:
            im = Image.new("RGB", (W, H), (43, 45, 66))
            d = ImageDraw.Draw(im)
            for y in range(0, H, 6):
                k = y / H
                d.rectangle([0, y, W, y + 6], fill=(int(43 + 30 * k), int(45 + 18 * k), int(66 + 50 * k)))
        self._bg_cache[bid] = im
        return im

    def sprite(self, cid: str, expr: str, dim: bool) -> Image.Image:
        key = (cid, expr, dim)
        if key in self._ch_cache:
            return self._ch_cache[key]
        p = self.assets["chars"].get(f"{cid}_{expr}") or self.assets["chars"].get(f"{cid}_neutral")
        th = int(H * 0.92)
        if p:
            im = Image.open(p).convert("RGBA")
            scale = th / im.height
            im = im.resize((max(1, round(im.width * scale)), th), Image.LANCZOS)
        else:
            c = self.chars.get(cid, {"name": cid, "color": "#7c5cff"})
            im = Image.new("RGBA", (int(th * 0.42), th), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle([0, int(th * 0.08), im.width - 1, th - 1], radius=40, fill=_hex(c["color"], 150),
                                outline=(255, 255, 255, 160), width=3)
            d.text((im.width // 2, th // 2), c["name"], font=self.f_name, fill="white", anchor="mm")
        if dim:
            rgb = ImageEnhance.Brightness(im.convert("RGB")).enhance(0.6)
            rgb.putalpha(im.getchannel("A"))
            im = rgb
        self._ch_cache[key] = im
        return im

    def watermark(self, img: Image.Image) -> None:
        d = ImageDraw.Draw(img, "RGBA")
        d.text((W - 18, 16), self.mark, font=self.f_mark, fill=(255, 255, 255, 150), anchor="ra",
               stroke_width=2, stroke_fill=(0, 0, 0, 90))

    def scene_frame(self, bid, shown: dict, pos: dict, speaker: str | None, name: str, color: str,
                    text: str) -> Image.Image:
        img = self.bg(bid).copy().convert("RGBA")
        xs = {"left": 0.18, "center": 0.5, "right": 0.82}
        for cid, expr in shown.items():
            sp = self.sprite(cid, expr, dim=speaker is not None and cid != speaker)
            x = int(W * xs.get(pos.get(cid, "center"), 0.5) - sp.width / 2)
            img.alpha_composite(sp, (x, H - sp.height))
        if text is not None:
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(ov)
            bx0, by0, bx1, by1 = int(W * 0.04), int(H * 0.70), int(W * 0.96), int(H * 0.96)
            d.rounded_rectangle([bx0, by0, bx1, by1], radius=14, fill=(12, 10, 32, 215),
                                outline=(255, 255, 255, 60), width=2)
            if name:
                nw = int(self.f_name.getlength(name)) + 36
                d.rounded_rectangle([bx0 + 22, by0 - 24, bx0 + 22 + nw, by0 + 20], radius=10, fill=_hex(color or "#7c5cff"))
                d.text((bx0 + 40, by0 - 2), name, font=self.f_name, fill="white", anchor="lm")
            lines = wrap(text, self.f_text, bx1 - bx0 - 70)[:4]
            y = by0 + 30
            for ln in lines:
                d.text((bx0 + 34, y), ln, font=self.f_text, fill="white")
                y += 42
            img.alpha_composite(ov)
        self.watermark(img)
        return img.convert("RGB")

    def choice_frame(self, base: Image.Image, options: list, chosen: int | None) -> Image.Image:
        img = base.copy().convert("RGBA")
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 90))
        d = ImageDraw.Draw(ov)
        n = len(options)
        bh, gap = 64, 18
        y = (H - (n * bh + (n - 1) * gap)) // 2 - 40
        for i, o in enumerate(options):
            sel = chosen == i
            fill = (0, 200, 255, 235) if sel else (124, 92, 255, 215 if chosen is None else 120)
            d.rounded_rectangle([W * 0.22, y, W * 0.78, y + bh], radius=14, fill=fill, outline=(255, 255, 255, 120), width=2)
            label = wrap(o["text"], self.f_text, int(W * 0.52))[0]
            d.text((W // 2, y + bh // 2), ("» " if sel else "") + label, font=self.f_text, fill="white", anchor="mm")
            y += bh + gap
        img.alpha_composite(ov)
        self.watermark(img)
        return img.convert("RGB")

    def card(self, bid, title: str, sub: str) -> Image.Image:
        img = self.bg(bid).copy()
        img = ImageEnhance.Brightness(img).enhance(0.35).convert("RGBA")
        d = ImageDraw.Draw(img)
        lines = wrap(title, self.f_title, int(W * 0.85))[:2]
        y = H // 2 - 40 - (len(lines) - 1) * 40
        for ln in lines:
            d.text((W // 2, y), ln, font=self.f_title, fill="white", anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))
            y += 80
        for ln in wrap(sub, self.f_sub, int(W * 0.75))[:3]:
            d.text((W // 2, y), ln, font=self.f_sub, fill=(230, 225, 255), anchor="mm")
            y += 40
        self.watermark(img)
        return img.convert("RGB")


def _positions(n: int) -> list[str]:
    return {1: ["center"], 2: ["left", "right"]}.get(n, ["left", "center", "right"] * 3)


# ---------------------------------------------------------------- main
def _run(cmd: list[str], timeout: int = 1800) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise VideoError("ffmpeg error: " + (r.stderr or "")[-600:])


def render_video(pid: int, story: dict, music_map: dict, route: dict, out: Path, progress=lambda m: None) -> Path:
    if not shutil.which("ffmpeg"):
        raise VideoError("เซิร์ฟเวอร์ยังไม่มี ffmpeg")
    assets = export.collect_assets(pid, story, music_map)
    painter = Painter(story, assets)
    events = walk(story, route)
    if not events:
        raise VideoError("ไม่มีฉากให้เรนเดอร์")
    is_th = any("฀" <= ch <= "๿" for ch in story["title"] + story["synopsis"])
    tmp_root = config.DATA_DIR / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=tmp_root))
    try:
        frames: list[tuple[Path, float]] = []
        segments: list[list] = []  # [music_path|None, seconds]

        def add(img: Image.Image, dur: float, mood: str | None) -> None:
            f = tmp / f"f{len(frames):05d}.jpg"
            img.save(f, "JPEG", quality=92)
            frames.append((f, dur))
            mf = assets["music"].get(mood) if mood else None
            if segments and segments[-1][0] == mf:
                segments[-1][1] += dur
            else:
                segments.append([mf, dur])

        first = events[0][1]
        add(painter.card(first.get("bg"), story["title"], story["synopsis"]), 5.0, first["mood"])
        total = sum(1 for e in events if e[0] in ("line", "choice"))
        done = 0
        shown, pos, scene = {}, {}, None
        last = None
        for ev in events:
            if ev[0] == "scene":
                scene = ev[1]
                shown = {}
                speakers = []
                for ln in scene["lines"]:
                    if ln["t"] == "say" and ln["who"] not in speakers:
                        speakers.append(ln["who"])
                pos = dict(zip(speakers, _positions(len(speakers))))
                img = painter.scene_frame(scene.get("bg"), {}, pos, None, "", "", None)
                add(img, 1.2, scene["mood"])
                last = img
                continue
            done += 1
            if done % 5 == 1:
                progress(f"กำลังวาดเฟรม {done}/{total}")
            if ev[0] == "line":
                ln = ev[2]
                if ln["t"] == "say" and ln["who"] in painter.chars:
                    c = painter.chars[ln["who"]]
                    shown[ln["who"]] = ln["expr"]
                    img = painter.scene_frame(scene.get("bg"), shown, pos, ln["who"], c["name"], c["color"], ln["text"])
                else:
                    img = painter.scene_frame(scene.get("bg"), shown, pos, None, "", "", ln["text"])
                dur = max(2.5, min(9.0, 1.6 + 0.075 * len(ln["text"])))
                add(img, dur, scene["mood"])
                last = img
            elif ev[0] == "choice":
                base = painter.scene_frame(scene.get("bg"), shown, pos, None, "", "", None)
                add(painter.choice_frame(base, ev[2]["options"], None), 2.5, scene["mood"])
                add(painter.choice_frame(base, ev[2]["options"], ev[3]), 1.5, scene["mood"])
        end_title = "— จบ —" if is_th else "— THE END —"
        end_sub = ("สร้างด้วย " if is_th else "Made with ") + painter.mark
        last_mood = scene["mood"] if scene else None
        add(painter.card(scene.get("bg") if scene else None, end_title, end_sub), 5.0, last_mood)

        # ---- video track
        progress("กำลังเข้ารหัสวิดีโอ...")
        lst = tmp / "frames.txt"
        with lst.open("w") as fh:
            for f, d in frames:
                fh.write(f"file '{f.name}'\nduration {d:.3f}\n")
            fh.write(f"file '{frames[-1][0].name}'\n")
        vid = tmp / "video.mp4"
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
              "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-movflags", "+faststart", str(vid)])

        # ---- audio track (music per scene, looped, with fades)
        progress("กำลังใส่เพลงประกอบ...")
        alist = tmp / "audio.txt"
        with alist.open("w") as fh:
            for i, (mf, dur) in enumerate(segments):
                seg = tmp / f"a{i:04d}.wav"
                if mf:
                    fade_out = max(0.0, dur - 1.2)
                    _run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(mf), "-t", f"{dur:.3f}",
                          "-af", f"volume=0.8,afade=t=in:d=0.8,afade=t=out:st={fade_out:.3f}:d=1.2",
                          "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(seg)])
                else:
                    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                          "-t", f"{dur:.3f}", "-c:a", "pcm_s16le", str(seg)])
                fh.write(f"file '{seg.name}'\n")
        aud = tmp / "audio.wav"
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(alist), "-c", "copy", str(aud)])

        progress("กำลังรวมภาพและเสียง...")
        out.parent.mkdir(parents=True, exist_ok=True)
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(vid), "-i", str(aud), "-c:v", "copy",
              "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)])
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
