# PhotoSnatcher Telethon Bot v13

Telegram bot for downloading accessible images from direct links and public Telegram channels, then delivering them as a ZIP file. It also includes a Render HTML/CSS/JS dashboard.

## v13 changes

- Removed the global active-job limit by default.
  - `MAX_ACTIVE_JOBS=0` means unlimited users can run downloads at the same time.
  - One user still cannot start a second channel download while their own first download is running.
- Removed daily user limits by default.
  - `USER_DAILY_CHANNEL_LIMIT=0` means unlimited channel jobs per day.
  - `USER_DAILY_DIRECT_LIMIT=0` means unlimited direct image links per day.
- Removed public photo cap by default.
  - `PUBLIC_MAX_PHOTOS_PER_JOB=0` means unlimited photos per channel job.
- Added privacy/storage controls.
  - `STORE_DOWNLOAD_HISTORY=false` stops storing per-photo message IDs and file paths.
  - `STORE_EVENT_DETAILS=false` stops saving full URLs/channel links in the events table.
  - Temporary images and ZIP files are deleted after delivery when `KEEP_TEMP_FILES=false`.
- Improved dashboard user stats.
  - Current active users.
  - Known users.
  - Left/blocked/inactive users.
  - Recent user status.
- Added `/privacy` command to show storage/privacy mode.

## Important limitation about left/deleted users

Telegram does not send a real-time notification when someone deletes the bot chat. The bot can mark a user as left/blocked only when Telegram rejects a message/file delivery to that user, or when the user interacts again and becomes active.

So the dashboard shows the best reliable values:

- `Current active users` = users currently known as reachable/active.
- `Left/blocked` = users Telegram rejected during delivery or messaging.
- `24h / 7d active` = users who interacted with the bot recently.

## Render settings

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
python bot.py
```

Recommended Render environment variables are in `.env.example`.

## Dashboard

Open your Render URL directly:

```text
https://your-render-service.onrender.com
```

For a public dashboard:

```env
DASHBOARD_ENABLED=true
DASHBOARD_PUBLIC=true
```

For a private dashboard:

```env
DASHBOARD_ENABLED=true
DASHBOARD_PUBLIC=false
DASHBOARD_SECRET=your_long_secret_here
```

Then open:

```text
https://your-render-service.onrender.com/dashboard?key=your_long_secret_here
```

## Main commands

```text
/start - welcome message
/help - usage instructions
/download <channel_link> - download channel photos
/status - current download progress
/pause - pause/resume own download
/cancel - cancel own download
/privacy - show storage/privacy settings
/whoami - show Telegram user ID
```

Admin commands:

```text
/admin - admin panel
/stats - bot statistics
/dashboard - dashboard link
/queue - active downloads
/users - recent users
/ban <id> - block user
/unban <id> - unblock user
/banlist - blocked users
/publicmode on|off|status - toggle public mode
/broadcast <message> - broadcast to users
/logs [lines] - show logs
/cleanup - delete temporary server files
/cancelall - cancel all active jobs
```

## Safety

The bot does not buy Telegram Stars, does not unlock paid media, and does not bypass locked/private content. It only downloads accessible photos.
