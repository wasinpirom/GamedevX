"""AI provider clients: OpenRouter (text + image) and fal.ai (image, background removal, music).

All model ids are admin-editable settings, so you can switch models without touching code.
Set MOCK_AI=1 to get placeholder results without calling any API (for testing).
"""
import asyncio
import base64
import io
import json
import math
import random
import re
import struct
import wave

import httpx
from PIL import Image, ImageDraw

from . import config, db

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
FAL_QUEUE = "https://queue.fal.run"


class ProviderError(Exception):
    pass


# ------------------------------------------------------------------ helpers
def _or_headers() -> dict:
    if not config.OPENROUTER_API_KEY:
        raise ProviderError("ยังไม่ได้ตั้งค่า OPENROUTER_API_KEY")
    return {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "HTTP-Referer": config.SITE_URL,
        "X-Title": config.SITE_NAME,
        "Content-Type": "application/json",
    }


def _fal_headers() -> dict:
    if not config.FAL_KEY:
        raise ProviderError("ยังไม่ได้ตั้งค่า FAL_KEY")
    return {"Authorization": f"Key {config.FAL_KEY}", "Content-Type": "application/json"}


def extract_json(text: str) -> dict:
    """Parse JSON from an LLM reply, tolerating ```json fences and extra prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ProviderError("AI ตอบกลับมาไม่เป็น JSON")


def data_uri(png_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(png_bytes).decode()


async def _download(url: str) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


def _find_urls(obj, keys=("url",)) -> list[str]:
    """Recursively collect URL strings from a fal.ai result (schemas differ per model)."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str) and (v.startswith("http") or v.startswith("data:")):
                found.append(v)
            else:
                found.extend(_find_urls(v, keys))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_find_urls(v, keys))
    return found


# ------------------------------------------------------------------ OpenRouter
async def llm_json(system: str, user: str, max_tokens: int = 12000) -> dict:
    if config.MOCK_AI:
        return {"_mock": True}
    body = {
        "model": db.get_setting("or_text_model"),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.8,
        "response_format": {"type": "json_object"},
    }
    last_err = None
    for attempt in range(2):
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(OR_URL, headers=_or_headers(), json=body)
        if r.status_code >= 400:
            # some models reject response_format; retry once without it
            if attempt == 0 and "response_format" in body:
                body.pop("response_format")
                last_err = r.text[:300]
                continue
            raise ProviderError(f"OpenRouter error {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise ProviderError(f"OpenRouter ตอบกลับผิดรูปแบบ: {str(data)[:300]}")
        try:
            return extract_json(content or "")
        except (ProviderError, json.JSONDecodeError) as e:
            last_err = str(e)
            continue
    raise ProviderError(f"สร้างเนื้อหาไม่สำเร็จ: {last_err}")


async def or_image(prompt: str, aspect: str, ref_png: bytes | None = None) -> bytes:
    content: list | str
    if ref_png:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri(ref_png)}},
        ]
    else:
        content = prompt
    body = {
        "model": db.get_setting("or_image_model"),
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": aspect},
    }
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(OR_URL, headers=_or_headers(), json=body)
    if r.status_code >= 400:
        raise ProviderError(f"OpenRouter image error {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError):
        raise ProviderError(f"OpenRouter ตอบกลับผิดรูปแบบ: {str(data)[:300]}")
    urls = _find_urls(msg.get("images") or [], keys=("url",))
    if not urls:
        raise ProviderError("โมเดลไม่ได้ส่งภาพกลับมา (ลองเปลี่ยนโมเดลภาพในหน้า admin)")
    return await _download(urls[0])


# ------------------------------------------------------------------ fal.ai (queue API)
async def fal_run(model_id: str, payload: dict, timeout_s: int = 600) -> dict:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{FAL_QUEUE}/{model_id}", headers=_fal_headers(), json=payload)
        if r.status_code >= 400:
            raise ProviderError(f"fal.ai error {r.status_code}: {r.text[:300]}")
        sub = r.json()
        status_url = sub.get("status_url")
        response_url = sub.get("response_url")
        if not status_url or not response_url:
            raise ProviderError(f"fal.ai ตอบกลับผิดรูปแบบ: {str(sub)[:300]}")
        waited = 0.0
        delay = 1.5
        while waited < timeout_s:
            await asyncio.sleep(delay)
            waited += delay
            delay = min(delay * 1.3, 6)
            s = await c.get(status_url, headers=_fal_headers())
            if s.status_code >= 400:
                continue
            st = s.json().get("status")
            if st == "COMPLETED":
                res = await c.get(response_url, headers=_fal_headers())
                if res.status_code >= 400:
                    raise ProviderError(f"fal.ai งานล้มเหลว: {res.text[:300]}")
                return res.json()
            if st in ("FAILED", "ERROR", "CANCELLED"):
                raise ProviderError(f"fal.ai งานล้มเหลว: {s.text[:300]}")
        raise ProviderError("fal.ai ใช้เวลานานเกินไป")


_FAL_ASPECT = {"16:9": "landscape_16_9", "2:3": "portrait_4_3", "1:1": "square_hd"}


async def fal_image(prompt: str, aspect: str, ref_png: bytes | None = None) -> bytes:
    if ref_png:
        model = db.get_setting("fal_image_edit_model")
        payload = {"prompt": prompt, "image_urls": [data_uri(ref_png)], "image_url": data_uri(ref_png),
                   "num_images": 1}
    else:
        model = db.get_setting("fal_image_model")
        payload = {"prompt": prompt, "image_size": _FAL_ASPECT.get(aspect, "landscape_16_9"),
                   "aspect_ratio": aspect, "num_images": 1}
    res = await fal_run(model, payload)
    urls = _find_urls(res.get("images") or res.get("image") or res)
    if not urls:
        raise ProviderError("fal.ai ไม่ได้ส่งภาพกลับมา")
    return await _download(urls[0])


async def remove_background(png: bytes) -> bytes | None:
    """Returns PNG with transparent background, or None if fal is not configured."""
    if config.MOCK_AI or not config.FAL_KEY:
        return None
    model = db.get_setting("fal_bg_remove_model")
    if not model:
        return None
    res = await fal_run(model, {"image_url": data_uri(png)})
    urls = _find_urls(res.get("image") or res)
    if not urls:
        return None
    return await _download(urls[0])


async def generate_image(prompt: str, aspect: str, ref_png: bytes | None = None) -> bytes:
    if config.MOCK_AI:
        await asyncio.sleep(0.3)
        return await asyncio.to_thread(mock_image, prompt, aspect)
    if db.get_setting("image_provider") == "fal":
        return await fal_image(prompt, aspect, ref_png)
    return await or_image(prompt, aspect, ref_png)


async def generate_music(prompt: str, seconds: int) -> tuple[bytes, str]:
    """Returns (audio bytes, file extension)."""
    if config.MOCK_AI:
        return mock_music(seconds), "wav"
    model = db.get_setting("fal_music_model")
    # input field names differ per model
    if "stable-audio" in model:
        payload = {"prompt": prompt, "seconds_total": min(seconds, 47)}
    elif "ace-step" in model:
        payload = {"tags": prompt, "lyrics": "[inst]", "duration": seconds}
    elif "elevenlabs" in model:
        payload = {"prompt": prompt, "music_length_ms": seconds * 1000, "force_instrumental": True}
    else:
        payload = {"prompt": prompt, "duration": seconds}
    res = await fal_run(model, payload, timeout_s=900)
    if isinstance(res.get("audio_url"), str):
        urls = [res["audio_url"]]
    else:
        urls = _find_urls(res.get("audio_file") or res.get("audio") or res)
    if not urls:
        raise ProviderError("fal.ai ไม่ได้ส่งไฟล์เพลงกลับมา")
    url = urls[0]
    ext = "mp3"
    m = re.search(r"\.(wav|mp3|ogg|flac|m4a)(\?|$)", url, re.I)
    if m:
        ext = m.group(1).lower()
    return await _download(url), ext


# ------------------------------------------------------------------ mock (MOCK_AI=1)
def mock_image(prompt: str, aspect: str) -> bytes:
    w, h = (1920, 1080) if aspect == "16:9" else (800, 1200)
    rnd = random.Random(prompt)
    base = tuple(rnd.randint(60, 200) for _ in range(3))
    img = Image.new("RGB", (w, h), (255, 255, 255) if aspect != "16:9" else base)
    d = ImageDraw.Draw(img)
    if aspect == "16:9":
        for i in range(0, h, 40):
            shade = tuple(max(0, min(255, c + (i // 40) * 3 - 30)) for c in base)
            d.rectangle([0, i, w, i + 40], fill=shade)
        d.text((20, 20), "MOCK " + prompt[:60], fill=(0, 0, 0))
    else:
        col = base
        d.ellipse([w * 0.3, h * 0.08, w * 0.7, h * 0.36], fill=col)
        d.rounded_rectangle([w * 0.2, h * 0.36, w * 0.8, h * 0.98], radius=60, fill=col)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def mock_music(seconds: int) -> bytes:
    rate = 22050
    seconds = min(seconds, 8)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        notes = [262, 330, 392, 523]
        frames = bytearray()
        for i in range(rate * seconds):
            f = notes[(i // (rate // 2)) % len(notes)]
            v = int(3000 * math.sin(2 * math.pi * f * i / rate))
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))
    return buf.getvalue()
