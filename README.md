# PhotoStiller Telethon v7

Telegram image downloader bot with:

- Direct image URL download
- Telegram channel photo history download
- Public mode support
- Private invite link support through `STRING_SESSION`
- Live editable progress bar with percentage
- Automatic deletion of temporary progress message after delivery

## Important Telegram link rules

Use real Telegram links:

```text
/download https://t.me/channelusername
/download @channelusername
/download https://t.me/+PRIVATE_INVITE_HASH
```

Do **not** use browser-only links like:

```text
https://web.telegram.org/k/#-2918294579
```

Those are internal Telegram Web links and cannot be resolved by Telethon. Open the channel in Telegram and copy the real `t.me` link.

## Render start command

```text
python bot.py
```

## Render build command

```text
pip install -r requirements.txt
```

## Required env

```env
API_ID=36948049
API_HASH=your_api_hash_here
BOT_TOKEN=your_new_bot_token_here
OWNER_ID=5147519186
```

## Recommended env for public mode

```env
PUBLIC_MODE=true
ALLOW_PRIVATE_LINKS_FOR_PUBLIC=false
MAX_ACTIVE_JOBS=2
```

## Private invite links

Generate your user session locally:

```bash
python generate_session.py
```

Then add this to Render:

```env
STRING_SESSION=your_long_string_session_here
```

Keep `ALLOW_PRIVATE_LINKS_FOR_PUBLIC=false` unless you want strangers to use your user session for private links.

## Progress bar

The bot now edits one live progress message approximately every second:

```text
██████░░░░░░░░░░░░ 33.0%
330/1000 photos processed
```

After the ZIP/file is delivered, the temporary progress message is deleted automatically.

Optional setting:

```env
PROGRESS_EDIT_INTERVAL_SECONDS=1
```

## Other env

```env
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
```
