# PhotoStiller Telegram Bot

A free-hostable Telegram bot that accepts a direct image URL, downloads the image, and sends it back in Telegram.

## Important security note

Do **not** put your real Telegram token in GitHub.

Use environment variables on Render/Koyeb instead.

If you already pasted your token anywhere public, open **@BotFather**, choose **PhotoStiller**, and regenerate/revoke only the PhotoStiller token.

## Files

```text
PhotoStiller-bot/
├── app.py
├── requirements.txt
├── Procfile
├── render.yaml
├── setup_webhook.py
├── .env.example
├── .gitignore
└── README.md
```

## Deploy on Render Free Web Service

1. Create a new GitHub repo, for example `PhotoStiller-bot`.
2. Upload all files from this project.
3. Go to Render.
4. Create a **New Web Service** from the GitHub repo.
5. Use these settings:

```text
Build Command:
pip install -r requirements.txt

Start Command:
gunicorn app:app
```

6. Add environment variables:

```env
BOT_TOKEN=your_new_photostiller_token_here
WEBHOOK_SECRET=make_a_long_random_secret_here
MAX_IMAGE_MB=10
```

7. Deploy.

## Set the Telegram webhook

After Render gives you a URL like:

```text
https://photostiller-bot.onrender.com
```

Run this command locally from the project folder:

### Windows PowerShell

```powershell
$env:BOT_TOKEN="your_new_photostiller_token_here"
$env:BASE_URL="https://your-render-app-name.onrender.com"
$env:WEBHOOK_SECRET="same_secret_you_added_on_render"
python setup_webhook.py
```

### macOS/Linux terminal

```bash
export BOT_TOKEN="your_new_photostiller_token_here"
export BASE_URL="https://your-render-app-name.onrender.com"
export WEBHOOK_SECRET="same_secret_you_added_on_render"
python setup_webhook.py
```

If it works, Telegram will return something like:

```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

## Test the bot

Open `@PhotoStiller_bot` in Telegram and send:

```text
/start
```

Then send a direct image URL:

```text
https://example.com/photo.jpg
```

Supported image types:

- JPG
- PNG
- WEBP
- GIF

## Notes

This bot uses webhook mode, which is better for free hosting than polling because the server does not need to constantly ask Telegram for updates.

The bot blocks localhost/private/internal network URLs for safety.
