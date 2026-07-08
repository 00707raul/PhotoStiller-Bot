# PhotoSnatcher Telethon v8

A production-ready Telegram image downloader bot using **Telethon / MTProto**.

It supports:

- Direct image URL download
- Telegram public channel photo-history download
- Owner-only private invite link download using `STRING_SESSION`
- Public mode for normal users
- Live progress bar with percentage, speed, ETA, pause and cancel
- ZIP delivery with a download report inside the archive
- Admin control panel, users, bans, broadcast, logs, queue, stats, runtime public-mode toggle
- Safer cleanup and disk checks for free Render hosting

> Important: locked/paid Telegram media cannot be bypassed. This bot downloads only accessible photos.

---

## Render commands

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
python bot.py
```

---

## Required Render environment variables

```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_new_botfather_token
OWNER_ID=your_numeric_telegram_id
```

---

## Recommended Render environment variables

```env
BOT_NAME=PhotoSnatcher
PUBLIC_MODE=true
ALLOW_PRIVATE_LINKS_FOR_PUBLIC=false
MAX_ACTIVE_JOBS=2
PROGRESS_EDIT_INTERVAL_SECONDS=1

MAX_CONCURRENT_DOWNLOADS=5
PROGRESS_UPDATE_INTERVAL=50
MAX_RETRIES=3
DOWNLOAD_TIMEOUT=30
MAX_STORAGE_GB=10
MAX_IMAGE_MB=50
MAX_URLS_PER_MESSAGE=5
REQUEST_TIMEOUT=25
TELEGRAM_ZIP_LIMIT_MB=2000
```

---

## New v8 environment variables

These are new or improved in v8:

```env
BOT_NAME=PhotoSnatcher
ADMIN_IDS=
PUBLIC_MAX_PHOTOS_PER_JOB=500
OWNER_MAX_PHOTOS_PER_JOB=0
USER_DAILY_CHANNEL_LIMIT=5
USER_DAILY_DIRECT_LIMIT=50
MIN_SECONDS_BETWEEN_JOBS=10
SCAN_ALL_MESSAGES_FOR_REPORTS=true
KEEP_TEMP_FILES=false
DELETE_PROGRESS_MESSAGES=true
```

Meaning:

- `ADMIN_IDS` — optional comma-separated extra admin IDs, for example `111,222`.
- `PUBLIC_MAX_PHOTOS_PER_JOB=500` — public users can download max 500 photos per channel job. Set `0` for unlimited.
- `OWNER_MAX_PHOTOS_PER_JOB=0` — owner/admin job limit. `0` means unlimited.
- `USER_DAILY_CHANNEL_LIMIT=5` — daily channel jobs per public user.
- `USER_DAILY_DIRECT_LIMIT=50` — daily direct image URL downloads per public user.
- `MIN_SECONDS_BETWEEN_JOBS=10` — anti-spam cooldown.
- `SCAN_ALL_MESSAGES_FOR_REPORTS=true` — scans all messages so the bot can report locked/paid posts seen.
- `KEEP_TEMP_FILES=false` — deletes temporary images after delivery/cancel/fail.
- `DELETE_PROGRESS_MESSAGES=true` — deletes progress bar message after job ends.

---

## Private invite links

Bot accounts cannot use some private invite methods. To support private links, generate a user session locally:

```bash
python generate_session.py
```

Then add this to Render:

```env
STRING_SESSION=your_long_string_session_here
```

Keep this safe:

```env
ALLOW_PRIVATE_LINKS_FOR_PUBLIC=false
```

That means public users can use public channels, but only you/admins can use private invite links.

---

## User commands

```text
/start - Welcome message
/help - Usage instructions
/whoami - Show your numeric Telegram ID
/limits - Show your daily limits
/download <channel_link> - Download accessible photos from channel history
/status - Show current progress
/pause - Pause/resume your current download
/cancel - Cancel your current download
/ping - Check bot uptime
```

Users can also send a Telegram channel link without `/download`; the bot will show a Start Download button.

Valid Telegram channel links:

```text
https://t.me/channelusername
t.me/channelusername
@channelusername
https://t.me/+PRIVATE_INVITE_HASH
https://t.me/joinchat/PRIVATE_INVITE_HASH
```

Invalid:

```text
https://web.telegram.org/k/#-123456
```

That is a browser-internal link. Use a real `t.me` link.

---

## Admin commands

```text
/admin - Inline admin panel
/stats - Uptime, users, jobs, disk, today usage
/queue - Active downloads
/users [number] - Recent users
/ban <user_id> - Block a user
/unban <user_id> - Unblock a user
/banlist - Show blocked users
/publicmode on|off|status - Change public mode without redeploy
/broadcast <message> - Send a message to all saved users
/logs [lines] - Show/send bot.log
/sessionstatus - Check STRING_SESSION status
/cleanup - Delete temp download files
/cancelall - Cancel every active job
```

---

## What changed in v8

- Renamed branding from PhotoStiller to PhotoSnatcher through `BOT_NAME`.
- Added admin panel `/admin`.
- Added `/stats`, `/queue`, `/users`, `/ban`, `/unban`, `/banlist`, `/broadcast`, `/logs`, `/publicmode`, `/limits`, `/ping`, `/cancelall`.
- Added SQLite user tracking and daily usage counters.
- Added public-user limits to prevent abuse on free Render.
- Added runtime public-mode toggle saved in SQLite.
- Added safer URL cleaning and fixed relative `og:image` links with `urljoin`.
- Added download manifest report inside ZIP.
- Added locked/paid media report counter when `SCAN_ALL_MESSAGES_FOR_REPORTS=true`.
- Added safer cleanup behavior controlled by `KEEP_TEMP_FILES` and `DELETE_PROGRESS_MESSAGES`.
- Removed `.git` folder from release ZIP.

---

## Deployment steps

1. Upload these files to GitHub.
2. In Render, set build command:
   ```text
   pip install -r requirements.txt
   ```
3. Set start command:
   ```text
   python bot.py
   ```
4. Add environment variables in Render.
5. Deploy with **Manual Deploy → Clear build cache & deploy**.
6. Test in Telegram:
   ```text
   /ping
   /admin
   /download https://t.me/channelusername
   ```

---

## Security notes

Never upload these to GitHub:

- `BOT_TOKEN`
- `API_HASH`
- `STRING_SESSION`
- `.env`

If any token/session is shared publicly, revoke/regenerate it.
