# PhotoSnatcher Telegram Bot

PhotoSnatcher is a Telethon-based Telegram bot for downloading accessible images from direct image links and public Telegram channels. It supports ZIP delivery, live progress tracking, public mode, admin controls, and a Render web dashboard.

## What is new in this version

The Render primary URL now opens a real HTML/CSS/JS dashboard instead of plain text.

Open your Render link:

```text
https://your-render-service.onrender.com
```

The dashboard shows:

- Unique users
- Active users in 24h / 7d
- Total downloaded files
- Today downloaded files
- Channel jobs
- Direct URL downloads
- Active downloads with progress bars
- Recent users
- Recent events
- Disk usage

The page auto-refreshes with JavaScript using `/api/stats`.

## Render start settings

Build command:

```bash
python -m pip install --upgrade pip && python -m pip install -r requirements.txt
```

Start command:

```bash
python bot.py
```

Recommended Render environment variable:

```env
PYTHON_VERSION=3.11.9
```

## Dashboard routes

```text
/              opens the stats website
/dashboard     opens the stats website
/api/stats     JSON stats API
/health        health check for cron-job.org
```

If you want the Render URL to show stats immediately, use:

```env
DASHBOARD_ENABLED=true
DASHBOARD_PUBLIC=true
```

If you want the dashboard private, use:

```env
DASHBOARD_ENABLED=true
DASHBOARD_PUBLIC=false
DASHBOARD_SECRET=your_long_secret_here
```

Then open:

```text
https://your-render-service.onrender.com/dashboard?key=your_long_secret_here
```

If you open the root URL while private mode is enabled, you will see a password page.

## Main Telegram commands

- `/start` - start the bot
- `/help` - show instructions
- `/download <telegram_channel_link>` - download accessible photos from a channel
- `/status` - show progress
- `/pause` - pause/resume current download
- `/cancel` - cancel current download
- `/limits` - show user limits
- `/whoami` - show numeric Telegram ID
- `/dashboard` - owner/admin dashboard link
- `/admin` - admin control panel
- `/stats` - bot stats
- `/cleanup` - delete temporary files

## Security

Never upload real secrets to GitHub:

- `BOT_TOKEN`
- `API_HASH`
- `STRING_SESSION`
- `.env`

Use Render Environment Variables only.
