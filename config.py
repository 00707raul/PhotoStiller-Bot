import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name, str(default)).strip()
    try:
        return float(value)
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _csv_int_env(name: str) -> set[int]:
    values = set()
    raw = os.getenv(name, "").strip()
    if not raw:
        return values
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError:
            continue
    return values


# Branding
BOT_NAME = os.getenv("BOT_NAME", "PhotoSnatcher").strip() or "PhotoSnatcher"

# Telegram / Telethon credentials
API_ID = _int_env("API_ID", 0)
API_HASH = os.getenv("API_HASH", "").strip().strip('"').strip("'")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
OWNER_ID = _int_env("OWNER_ID", 0)
ADMIN_IDS = _csv_int_env("ADMIN_IDS")
if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)

# Access control
PUBLIC_MODE = _bool_env("PUBLIC_MODE", False)
ALLOW_PRIVATE_LINKS_FOR_PUBLIC = _bool_env("ALLOW_PRIVATE_LINKS_FOR_PUBLIC", False)
MAX_ACTIVE_JOBS = _int_env("MAX_ACTIVE_JOBS", 0)  # 0 = unlimited global concurrent jobs

# Optional user account session, needed for private invite links like https://t.me/+XXXX.
STRING_SESSION = os.getenv("STRING_SESSION", "").strip().strip('"').strip("'")

# Abuse protection / public limits
# 0 = unlimited. Owner/admins are controlled by OWNER_MAX_PHOTOS_PER_JOB.
PUBLIC_MAX_PHOTOS_PER_JOB = _int_env("PUBLIC_MAX_PHOTOS_PER_JOB", 0)
OWNER_MAX_PHOTOS_PER_JOB = _int_env("OWNER_MAX_PHOTOS_PER_JOB", 0)
USER_DAILY_CHANNEL_LIMIT = _int_env("USER_DAILY_CHANNEL_LIMIT", 0)
USER_DAILY_DIRECT_LIMIT = _int_env("USER_DAILY_DIRECT_LIMIT", 0)
MIN_SECONDS_BETWEEN_JOBS = _int_env("MIN_SECONDS_BETWEEN_JOBS", 0)

# Download configuration
MAX_CONCURRENT_DOWNLOADS = _int_env("MAX_CONCURRENT_DOWNLOADS", 5)
PROGRESS_UPDATE_INTERVAL = _int_env("PROGRESS_UPDATE_INTERVAL", 50)
PROGRESS_EDIT_INTERVAL_SECONDS = max(1, _int_env("PROGRESS_EDIT_INTERVAL_SECONDS", 1))
MAX_RETRIES = _int_env("MAX_RETRIES", 3)
DOWNLOAD_TIMEOUT = _int_env("DOWNLOAD_TIMEOUT", 30)
MAX_STORAGE_GB = _float_env("MAX_STORAGE_GB", 10.0)
MAX_IMAGE_MB = _int_env("MAX_IMAGE_MB", 50)
MAX_URLS_PER_MESSAGE = _int_env("MAX_URLS_PER_MESSAGE", 5)
REQUEST_TIMEOUT = _int_env("REQUEST_TIMEOUT", 25)
SCAN_ALL_MESSAGES_FOR_REPORTS = _bool_env("SCAN_ALL_MESSAGES_FOR_REPORTS", True)
KEEP_TEMP_FILES = _bool_env("KEEP_TEMP_FILES", False)
DELETE_PROGRESS_MESSAGES = _bool_env("DELETE_PROGRESS_MESSAGES", True)

# Privacy/storage control
# False = do not store per-photo file paths/message IDs for resume. This keeps the DB small.
STORE_DOWNLOAD_HISTORY = _bool_env("STORE_DOWNLOAD_HISTORY", False)
# False = events store only safe summary text, not full user links/URLs.
STORE_EVENT_DETAILS = _bool_env("STORE_EVENT_DETAILS", False)
# Delete old analytics rows/events automatically. 0 = keep aggregate analytics forever.
ANALYTICS_RETENTION_DAYS = _int_env("ANALYTICS_RETENTION_DAYS", 30)

# Runtime folders
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_ROOT = BASE_DIR / "downloads"
DATA_DIR = BASE_DIR / "data"
LOG_FILE = BASE_DIR / "bot.log"
DATABASE_PATH = DATA_DIR / "downloads.sqlite3"
SESSION_NAME = str(DATA_DIR / "photosnatcher_bot")

# Telegram delivery limits / behaviour
TELEGRAM_ZIP_LIMIT_BYTES = _int_env("TELEGRAM_ZIP_LIMIT_MB", 2000) * 1024 * 1024
ALBUM_BATCH_SIZE = 10


# Web dashboard configuration
DASHBOARD_ENABLED = _bool_env("DASHBOARD_ENABLED", True)
DASHBOARD_PUBLIC = _bool_env("DASHBOARD_PUBLIC", False)
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "").strip().strip('"').strip("'")
DASHBOARD_REFRESH_SECONDS = max(5, _int_env("DASHBOARD_REFRESH_SECONDS", 15))
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "").strip().rstrip("/")

# Render/free hosting health server
PORT = _int_env("PORT", 10000)
