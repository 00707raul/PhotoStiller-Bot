# PhotoSnatcher Telegram Bot

PhotoSnatcher is a Telethon-based Telegram bot for downloading accessible images from direct image links and public Telegram channels. It can create ZIP files, show live progress, and display usage statistics on a small Render dashboard.

## Main commands

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

The code also includes a Python 3.14 event-loop compatibility fix, so it can start even if Render uses a newer Python version.

## Dashboard

Dashboard routes:

- `/health`
- `/dashboard?key=YOUR_DASHBOARD_SECRET`
- `/api/stats?key=YOUR_DASHBOARD_SECRET`

Keep `DASHBOARD_PUBLIC=false` and use a strong `DASHBOARD_SECRET`.

## Security

Never upload real secrets to GitHub:

- `BOT_TOKEN`
- `API_HASH`
- `STRING_SESSION`
- `.env`

Use Render Environment Variables only.
