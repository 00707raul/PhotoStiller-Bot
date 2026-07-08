# PhotoSnatcher Telegram Bot

PhotoSnatcher is a Telegram bot for saving accessible images from direct image URLs and public Telegram channels. It supports channel photo downloads, ZIP delivery, progress bars, public mode, admin controls, and a small Render web dashboard.

## Main features

- Direct image URL download
- Telegram channel photo download with `/download <channel_link>`
- ZIP delivery inside Telegram
- Live progress bar with Telegram upload progress
- Public mode with daily limits
- Owner/admin control commands
- Ban/unban system
- SQLite user and usage tracking
- Render `/health` endpoint for cron-job pings
- Render `/dashboard` website for stats

## Important safety note

The bot downloads only accessible photos. It does not buy, unlock, or bypass Telegram paid media, Telegram Stars, private content, or locked posts.

## Render start settings

Build Command:

```bash
pip install -r requirements.txt
```

Start Command:

```bash
python bot.py
```

Cron-job / keep-alive URL:

```text
https://your-render-service.onrender.com/health
```

## Required Render env

```env
API_ID=12345678
API_HASH=your_api_hash_here
BOT_TOKEN=your_botfather_token_here
OWNER_ID=your_numeric_telegram_id
BOT_NAME=PhotoSnatcher
```

## Optional but recommended env

```env
STRING_SESSION=your_telethon_user_string_session
PUBLIC_MODE=true
ALLOW_PRIVATE_LINKS_FOR_PUBLIC=false
MAX_ACTIVE_JOBS=2

PUBLIC_MAX_PHOTOS_PER_JOB=500
OWNER_MAX_PHOTOS_PER_JOB=0
USER_DAILY_CHANNEL_LIMIT=5
USER_DAILY_DIRECT_LIMIT=50
MIN_SECONDS_BETWEEN_JOBS=10

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
SCAN_ALL_MESSAGES_FOR_REPORTS=true
KEEP_TEMP_FILES=false
DELETE_PROGRESS_MESSAGES=true
```

## Web dashboard env

The dashboard shows:

- Unique users
- Active users in 24h and 7d
- Total downloaded files
- Today downloaded files
- Total direct URL downloads
- Total channel jobs
- Active downloads
- Recent users
- Recent events
- Disk usage

Add these to Render:

```env
DASHBOARD_ENABLED=true
DASHBOARD_PUBLIC=false
DASHBOARD_SECRET=change_this_to_a_long_random_secret
DASHBOARD_REFRESH_SECONDS=15
WEB_BASE_URL=https://your-render-service.onrender.com
```

Open the dashboard:

```text
https://your-render-service.onrender.com/dashboard?key=change_this_to_a_long_random_secret
```

Or send this command to the bot as owner/admin:

```text
/dashboard
```

The bot will send your private dashboard link.

## User commands

```text
/start - Start the bot
/help - Show help
/whoami - Show your Telegram numeric ID
/limits - Show your daily limits
/download <channel_link> - Download accessible photos from a Telegram channel
/status - Show current progress
/pause - Pause/resume current download
/cancel - Stop current download
/ping - Check bot uptime
```

## Admin commands

```text
/admin - Admin control panel
/stats - Telegram stats summary
/dashboard - Get private Render dashboard URL
/queue - Active downloads
/users [limit] - Recent users
/ban <user_id> - Block user
/unban <user_id> - Unblock user
/banlist - Show blocked users
/publicmode on|off|status - Change public mode without Render redeploy
/broadcast <message> - Message all saved users
/logs [lines] - Show/send recent logs
/cleanup - Delete temporary downloaded files
/cancelall - Cancel all active jobs
/sessionstatus - Check STRING_SESSION status
```

## Generate STRING_SESSION

Run locally:

```bash
pip install -r requirements.txt
python generate_session.py
```

Paste the generated `STRING_SESSION` into Render env only. Do not publish it.

## Notes about stats persistence

Stats are stored in SQLite inside the app folder. On free Render, local files can reset after redeploys/restarts. For permanent long-term analytics, use a persistent database later, such as PostgreSQL/Supabase.
