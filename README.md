# PhotoSnatcher Telethon Bot v15

Production Telegram bot with a built-in Render website dashboard.

## Main features

- Download accessible images from direct image links.
- Download accessible photos from public Telegram channels.
- Optional user `STRING_SESSION` for channels your account can access.
- Live Telegram progress bar.
- ZIP delivery.
- Per-user single active job protection: each user must finish their current job before starting a new one.
- No global job limit by default: many different users can run jobs at the same time.
- Dashboard website opens from the Render root URL `/`.
- Dashboard API: `/api/stats`.
- Health check: `/health`.
- Privacy mode: temporary ZIP/images are deleted after delivery; detailed download history is disabled by default.

## Render start command

```bash
python bot.py
```

## Required env

```env
API_ID=36948049
API_HASH=PASTE_IN_RENDER_ONLY
BOT_TOKEN=PASTE_NEW_BOT_TOKEN_IN_RENDER_ONLY
OWNER_ID=5147519186
STRING_SESSION=PASTE_STRING_SESSION_IN_RENDER_ONLY

BOT_NAME=PhotoSnatcher
PUBLIC_MODE=true
ALLOW_PRIVATE_LINKS_FOR_PUBLIC=false
ADMIN_IDS=

MAX_ACTIVE_JOBS=0
PUBLIC_MAX_PHOTOS_PER_JOB=0
OWNER_MAX_PHOTOS_PER_JOB=0
USER_DAILY_CHANNEL_LIMIT=0
USER_DAILY_DIRECT_LIMIT=0
MIN_SECONDS_BETWEEN_JOBS=0

MAX_CONCURRENT_DOWNLOADS=5
PROGRESS_UPDATE_INTERVAL=50
PROGRESS_EDIT_INTERVAL_SECONDS=1
MAX_RETRIES=3
DOWNLOAD_TIMEOUT=30
MAX_STORAGE_GB=10
MAX_IMAGE_MB=50
MAX_URLS_PER_MESSAGE=5
REQUEST_TIMEOUT=25
TELEGRAM_ZIP_LIMIT_MB=2000

KEEP_TEMP_FILES=false
DELETE_PROGRESS_MESSAGES=true
STORE_DOWNLOAD_HISTORY=false
STORE_EVENT_DETAILS=false
ANALYTICS_RETENTION_DAYS=30
SCAN_ALL_MESSAGES_FOR_REPORTS=true

DASHBOARD_ENABLED=true
DASHBOARD_PUBLIC=true
DASHBOARD_SECRET=CHANGE_THIS_TO_RANDOM_TEXT
DASHBOARD_REFRESH_SECONDS=15
WEB_BASE_URL=https://your-render-service.onrender.com
PYTHON_VERSION=3.11.9
```

## Website dashboard

Open your Render URL directly:

```text
https://your-render-service.onrender.com
```

You will see an HTML/CSS/JS stats dashboard with users, downloads, active jobs, daily usage, recent users, and disk info.

## Bot commands

```text
/start
/help
/download <telegram_channel_link>
/status
/cancel
/cleanup
/sessionstatus
/dashboard
/stats
/admin
/privacy
```

Do not upload `.env`, bot tokens, API hash, or STRING_SESSION to GitHub.


## v15 fix

- Fixed the Render dashboard Internal Server Error caused by JavaScript template syntax inside Python rendering.
- Opening the Render root URL now loads the HTML/CSS/JS statistics dashboard directly when `DASHBOARD_PUBLIC=true`.
