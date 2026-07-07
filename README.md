# PhotoStiller Telegram Bot - Polling Version

This bot uses Telegram **polling**, not webhook.

It accepts direct image URL links, downloads the image, and sends it back in Telegram.

## Important security step

If you pasted your token anywhere public or into ChatGPT, go to `@BotFather` and revoke/regenerate only the **PhotoStiller_bot** token.

Do not revoke `TradePilot007_bot` if you want to keep it working.

## Files

```text
PhotoStiller-bot/
├── bot.py
├── requirements.txt
├── Procfile
├── render.yaml
├── runtime.txt
├── .env.example
├── .gitignore
└── README.md
```

## Local test on your PC

1. Install Python 3.11+
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create `.env` from `.env.example`:

```env
BOT_TOKEN=your_new_photostiller_token
MAX_IMAGE_MB=10
MAX_URLS_PER_MESSAGE=5
```

4. Run:

```bash
python bot.py
```

Open Telegram and send `/start` to `@PhotoStiller_bot`.

## Deploy on Render without webhook

This uses polling, but it also starts a tiny Flask web server for health checks. That web server is **not** a Telegram webhook.

Render settings:

```text
Build Command:
pip install -r requirements.txt

Start Command:
python bot.py
```

Environment variables on Render:

```env
BOT_TOKEN=your_new_photostiller_token
MAX_IMAGE_MB=10
MAX_URLS_PER_MESSAGE=5
```

After deploy, Render gives you a URL like:

```text
https://photostiller-bot.onrender.com
```

Open:

```text
https://photostiller-bot.onrender.com/health
```

If it shows `ok: true`, the web service is running.

## Very important about free hosting

Polling needs the program to stay alive. Free hosts may sleep when they do not receive web traffic. If the app sleeps, Telegram messages will not wake it because this bot does not use webhook.

For Render Free, use a free uptime monitor to ping this URL every 5 minutes:

```text
https://your-render-app-name.onrender.com/health
```

Examples of uptime monitors:

- UptimeRobot
- Better Stack free monitor
- cron-job.org

## Best truly free 24/7 polling option

A free VPS is better for polling. Oracle Cloud Always Free can run a small Linux server, but setup is harder than Render.

## Commands

- `/start` - intro message
- `/help` - usage help

## Notes

- Send direct image links, for example `.jpg`, `.png`, `.webp`, `.gif`.
- The bot blocks private/localhost URLs for safety.
- Do not upload `.env` to GitHub.
