"""Configuration from environment variables (set these in Coolify)."""
import os
import secrets
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DATA_DIR = Path(_env("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "gamedevx.db"
PROJECTS_DIR = DATA_DIR / "projects"
MUSIC_DIR = DATA_DIR / "music"
BANNER_DIR = DATA_DIR / "banners"
BOARD_DIR = DATA_DIR / "board"

SITE_URL = _env("SITE_URL", "https://gamedevx.com")
SITE_NAME = _env("SITE_NAME", "GamedevX")

OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
FAL_KEY = _env("FAL_KEY")

# Set MOCK_AI=1 to test the whole system without spending API money
MOCK_AI = _env("MOCK_AI", "0") in ("1", "true", "yes")

ADMIN_EMAIL = _env("ADMIN_EMAIL").lower()
ADMIN_PASSWORD = _env("ADMIN_PASSWORD")

LINE_OA_ID = _env("LINE_OA_ID", "@wasin")
KWAY_SHOP_URL = _env("KWAY_SHOP_URL", "https://kway.app/shop")

COOKIE_SECURE = _env("COOKIE_SECURE", "1") in ("1", "true", "yes")


def _secret_key() -> str:
    key = _env("SECRET_KEY")
    if key:
        return key
    # Persist a generated key so sessions survive restarts
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / ".secret_key"
    if f.exists():
        return f.read_text().strip()
    key = secrets.token_urlsafe(48)
    f.write_text(key)
    return key


SECRET_KEY = _secret_key()

# Defaults for admin-editable settings (stored in DB, editable on /admin)
DEFAULT_SETTINGS = {
    # credit costs
    "cost_story": "10",
    "cost_scene_rewrite": "2",
    "cost_character_image": "3",
    "cost_background_image": "3",
    "cost_music": "10",
    "cost_video": "5",
    "welcome_credits": "30",
    # models (change any time on the admin page)
    "image_provider": "openrouter",  # openrouter | fal
    "or_text_model": "anthropic/claude-sonnet-4.5",
    "or_image_model": "google/gemini-2.5-flash-image",
    "fal_image_model": "fal-ai/flux/dev",
    "fal_image_edit_model": "fal-ai/nano-banana/edit",
    "fal_bg_remove_model": "fal-ai/birefnet",
    "fal_music_model": "fal-ai/stable-audio",
    "music_seconds": "60",
    # top-up page text
    "topup_packages": "100 เครดิต = 99 บาท\n300 เครดิต = 249 บาท\n1000 เครดิต = 699 บาท",
    "announcement": "",
    # retro portal home page
    "site_tagline": "ศูนย์รวมนักพัฒนาเกมไทย — สร้างเกม Visual Novel ด้วย AI",
    "history_title": "Wasin Pirom's GamedevX History",
    "site_title": "GamedevX New Generation",
    "left_title": "บทสัมภาษณ์นักพัฒนาเกม",
    # community web board
    "board_categories": "พูดคุยทั่วไป\nถาม-ตอบ ปัญหาการพัฒนาเกม\nผลงานเกม / แชร์เกม\n3D / กราฟิก / Game Art\nเกม Visual Novel ด้วย AI\nข่าว / ประกาศ",
    "allow_guest_post": "0",
    "bad_words": "",
    "board_per_page": "30",
}

MOODS = {
    "happy": "สดใส",
    "calm": "สงบ ผ่อนคลาย",
    "romantic": "โรแมนติก",
    "sad": "เศร้า",
    "tense": "ลุ้นระทึก",
    "horror": "สยองขวัญ",
    "funny": "ตลก",
    "mystery": "ลึกลับ",
    "epic": "ยิ่งใหญ่ ต่อสู้",
    "ending": "ฉากจบ",
}

EXPRESSIONS = {
    "neutral": "ปกติ",
    "happy": "ยิ้ม",
    "sad": "เศร้า",
    "angry": "โกรธ",
    "surprised": "ตกใจ",
    "shy": "เขิน",
}

ART_STYLES = {
    "anime": "anime visual novel style, clean lineart, soft cel shading",
    "thai_art": "modern Thai illustration inspired by traditional Thai painting, gold accents",
    "watercolor": "soft watercolor storybook illustration",
    "semi_real": "semi-realistic digital painting, cinematic lighting",
    "chibi": "cute chibi style, bold outlines, pastel colors",
    "pixel": "high quality pixel art, limited palette",
}
