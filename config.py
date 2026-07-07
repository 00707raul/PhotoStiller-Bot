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


# Telegram / Telethon credentials
API_ID = _int_env("API_ID", 0)
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = _int_env("OWNER_ID", 0)

# Download configuration
MAX_CONCURRENT_DOWNLOADS = _int_env("MAX_CONCURRENT_DOWNLOADS", 5)
PROGRESS_UPDATE_INTERVAL = _int_env("PROGRESS_UPDATE_INTERVAL", 50)
MAX_RETRIES = _int_env("MAX_RETRIES", 3)
DOWNLOAD_TIMEOUT = _int_env("DOWNLOAD_TIMEOUT", 30)
MAX_STORAGE_GB = _float_env("MAX_STORAGE_GB", 10.0)
MAX_IMAGE_MB = _int_env("MAX_IMAGE_MB", 10)
MAX_URLS_PER_MESSAGE = _int_env("MAX_URLS_PER_MESSAGE", 5)
REQUEST_TIMEOUT = _int_env("REQUEST_TIMEOUT", 25)

# Runtime folders
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_ROOT = BASE_DIR / "downloads"
DATA_DIR = BASE_DIR / "data"
LOG_FILE = BASE_DIR / "bot.log"
DATABASE_PATH = DATA_DIR / "downloads.sqlite3"
SESSION_NAME = str(DATA_DIR / "photostiller_bot")

# Telegram delivery limits / behaviour
TELEGRAM_ZIP_LIMIT_BYTES = _int_env("TELEGRAM_ZIP_LIMIT_MB", 2000) * 1024 * 1024
ALBUM_BATCH_SIZE = 10

# Render/free hosting health server
PORT = _int_env("PORT", 10000)
