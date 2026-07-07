# PhotoStiller Telethon v6

Telegram bot for:

1. Downloading direct image URLs.
2. Downloading photos from Telegram public channels.
3. Downloading photos from private invite links when `STRING_SESSION` is configured.
4. Optional public mode so everyone can use the bot.

## Render start command

```bash
python bot.py
```

## Required Render environment variables

```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_telegram_user_id
```

## User session for private invite links

Private invite links such as `https://t.me/+XXXX` need a user session:

```env
STRING_SESSION=your_telethon_string_session
```

Generate it locally:

```bash
pip install -r requirements.txt
python generate_session.py
```

## Make the bot public

To let anyone use the bot, add this in Render:

```env
PUBLIC_MODE=true
```

Recommended safe public settings:

```env
PUBLIC_MODE=true
ALLOW_PRIVATE_LINKS_FOR_PUBLIC=false
MAX_ACTIVE_JOBS=2
```

With this setup:

- anyone can send direct image URLs;
- anyone can download images from public Telegram channels;
- only the owner can use private invite links;
- `/cleanup` and `/sessionstatus` stay owner-only.

## Dangerous option

This allows public users to use your `STRING_SESSION`/Telegram user account for private invite links:

```env
ALLOW_PRIVATE_LINKS_FOR_PUBLIC=true
```

Use it only if you fully trust your users.

## Commands

```text
/start
/help
/download https://t.me/channelname
/status
/cancel
/pause
/cleanup
/sessionstatus
```

## Example usage

```text
/download https://t.me/SomePublicChannel
```

Or send only:

```text
https://t.me/SomePublicChannel
```

Then press **Start Download**.
