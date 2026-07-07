# PhotoStiller Bot - Telethon Version

This version keeps the original direct image URL downloader and adds a new mechanic:

**Telegram channel image downloader** using **Telethon / MTProto**.

The bot can:

- Download direct image URLs.
- Accept Telegram channel links.
- Scan channel history for photos only.
- Download all channel photos.
- Save progress in SQLite.
- Resume incomplete downloads after restart when files still exist.
- Create a ZIP archive and send it to the owner.
- Send albums in batches if ZIP is too large.
- Support `/status`, `/cancel`, `/pause`, inline buttons, and owner-only access.

---

## Project files

```text
PhotoStiller-Telethon-v3/
├── bot.py
├── channel_downloader.py
├── config.py
├── database.py
├── logger_setup.py
├── utils.py
├── requirements.txt
├── Procfile
├── render.yaml
├── runtime.txt
├── .python-version
├── .env.example
├── .gitignore
└── README.md
```

---

## Important Telegram rule

For channel history downloading, add **PhotoStiller_bot** as an **admin** in the target Telegram channel first.

The bot will reject the job with:

```text
This bot must be added as an admin to this channel to read history.
```

For private channels/invite links, the bot must already have access. Bots normally cannot join private invite links by themselves like a normal user.

---

## Environment variables for Render

Add these in **Render → Environment → Add Environment Variable**:

```env
API_ID=your_api_id_from_my_telegram_org
API_HASH=your_api_hash_from_my_telegram_org
BOT_TOKEN=your_new_photostiller_bot_token
OWNER_ID=your_numeric_telegram_user_id
MAX_CONCURRENT_DOWNLOADS=5
PROGRESS_UPDATE_INTERVAL=50
MAX_RETRIES=3
DOWNLOAD_TIMEOUT=30
MAX_STORAGE_GB=10
MAX_IMAGE_MB=10
MAX_URLS_PER_MESSAGE=5
REQUEST_TIMEOUT=25
TELEGRAM_ZIP_LIMIT_MB=2000
```

Do **not** add webhook variables. This bot uses polling through Telethon.

---

## Where to get API_ID and API_HASH

1. Open `https://my.telegram.org/apps`
2. Log in with your Telegram account.
3. Create an app.
4. Copy:
   - `api_id`
   - `api_hash`

---

## Where to get OWNER_ID

Open Telegram and message:

```text
@userinfobot
```

Copy your numeric Telegram ID and put it as:

```env
OWNER_ID=123456789
```

Only this user can use the bot.

---

## Render settings

Use:

```text
Build Command:
pip install -r requirements.txt
```

```text
Start Command:
python bot.py
```

The bot starts a small Flask health page on `/health`, so Render can see that the service is alive.

---

## Commands

```text
/start
```
Welcome message.

```text
/help
```
Show usage instructions.

```text
/download https://t.me/SomeChannel
```
Validate channel and show a Start Download button.

```text
/status
```
Show current progress.

```text
/cancel
```
Cancel current download.

```text
/pause
```
Pause or resume current download.

---

## Link formats accepted

```text
https://t.me/SomeChannel
t.me/SomeChannel
@SomeChannel
https://t.me/joinchat/XXXX
https://t.me/+XXXX
```

Private invite links only work if the bot already has access.

---

## Direct image URL downloader

You can still send direct image links like:

```text
https://example.com/image.jpg
```

The bot downloads and sends the image back.

For webpage links, the bot tries to find `og:image` preview images, but some websites block server requests.

---

## Safety

Never upload your `.env` file to GitHub.

If you pasted your token anywhere public, revoke only **PhotoStiller_bot** token in **@BotFather**, then add the new token to Render.

---

## Notes for free hosting

Render Free can sleep. This project includes a `/health` page, but polling bots work best on always-on hosting. If Render sleeps, use an uptime monitor to ping:

```text
https://your-render-app.onrender.com/health
```

