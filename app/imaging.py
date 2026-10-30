"""Image post-processing so assets are game-ready (correct size, transparent sprites)."""
import io

from PIL import Image, ImageChops, ImageDraw, ImageFilter

BG_SIZE = (1920, 1080)
CHAR_MAX_H = 1000
CHAR_MAX_W = 900


def process_background(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    tw, th = BG_SIZE
    scale = max(tw / img.width, th / img.height)
    img = img.resize((max(tw, round(img.width * scale)), max(th, round(img.height * scale))), Image.LANCZOS)
    left = (img.width - tw) // 2
    top = (img.height - th) // 2
    img = img.crop((left, top, left + tw, top + th))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


_SENTINEL = (1, 254, 3)


def _strip_plain_background(img: Image.Image) -> Image.Image:
    """Fallback when no background-removal model: flood-fill near-white/near-uniform
    background from the image border and make it transparent."""
    full = img.convert("RGB")
    # flood fill on a small copy (fast), then scale the mask back up
    small = full.copy()
    small.thumbnail((360, 360))
    w, h = small.size
    seeds = []
    step = max(1, min(w, h) // 20)
    for x in range(0, w, step):
        seeds += [(x, 0), (x, h - 1)]
    for y in range(0, h, step):
        seeds += [(0, y), (w - 1, y)]
    for s in seeds:
        px = small.getpixel(s)
        if px == _SENTINEL:
            continue
        if sum(px) / 3 < 200:  # only fill light backgrounds
            continue
        ImageDraw.floodfill(small, s, _SENTINEL, thresh=38)
    diff = ImageChops.difference(small, Image.new("RGB", small.size, _SENTINEL))
    r, g, b = diff.split()
    keep_small = ImageChops.add(ImageChops.add(r, g), b).point(lambda v: 255 if v > 0 else 0)
    # shrink the "keep" area by 1px (small scale) so no white halo stays around the character;
    # the light-pixel check below protects dark/colored edge pixels
    keep_small = keep_small.filter(ImageFilter.MinFilter(3))
    keep = keep_small.resize(full.size, Image.BILINEAR).point(lambda v: 255 if v >= 128 else 0)
    # only clear pixels that are actually light at full resolution (keeps edges crisp)
    light = full.convert("L").point(lambda v: 255 if v >= 200 else 0)
    bg_mask = ImageChops.multiply(ImageChops.invert(keep), light)  # 255 where background
    out = img.convert("RGBA")
    alpha = ImageChops.subtract(out.getchannel("A"), bg_mask)
    out.putalpha(alpha)
    return out


def has_transparency(img: Image.Image) -> bool:
    if img.mode != "RGBA":
        return False
    lo, hi = img.getchannel("A").getextrema()
    return lo < 250


def process_character(raw: bytes, already_cut: bool) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGBA")
    if not already_cut and not has_transparency(img):
        img = _strip_plain_background(img)
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    scale = min(CHAR_MAX_H / img.height, CHAR_MAX_W / img.width)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def thumbnail(raw: bytes, max_side: int = 360) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    if img.mode == "RGBA":
        img.save(buf, "PNG")
    else:
        img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def normalize_reference(raw: bytes, max_side: int = 1024) -> bytes:
    """Reference photo / sketch: fix orientation, flatten transparency, limit size."""
    from PIL import ImageOps
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
