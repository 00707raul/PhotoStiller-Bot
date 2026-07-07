import asyncio
import secrets
import threading
from pathlib import Path

from flask import Flask
from telethon import Button, TelegramClient, events
from telethon.errors import RPCError

from channel_downloader import ChannelImageDownloader
from config import (
    API_HASH,
    API_ID,
    BOT_TOKEN,
    DATA_DIR,
    DOWNLOAD_ROOT,
    MAX_URLS_PER_MESSAGE,
    OWNER_ID,
    PORT,
    SESSION_NAME,
)
from database import DownloadDB
from logger_setup import setup_logging
from utils import (
    cleanup_paths,
    download_direct_image,
    ensure_dirs,
    extract_og_image,
    extract_urls,
    is_telegram_channel_link,
    is_valid_http_url,
    normalize_channel_input,
)

logger = setup_logging()
db = DownloadDB()
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
downloader = ChannelImageDownloader(client, db, logger)
pending_channel_links = {}


def make_start_button(channel_link: str):
    key = secrets.token_urlsafe(8)
    pending_channel_links[key] = channel_link
    return Button.inline("Start Download", data=f"start:{key}".encode("utf-8"))

health_app = Flask(__name__)


@health_app.route("/")
def index():
    return "PhotoStiller bot is running."


@health_app.route("/health")
def health():
    return {"status": "ok", "bot": "PhotoStiller"}


def run_health_server():
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def check_config() -> None:
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not OWNER_ID:
        missing.append("OWNER_ID")
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")


def owner_only(func):
    async def wrapper(event):
        sender_id = event.sender_id
        if sender_id != OWNER_ID:
            await event.reply("❌ This bot is private. You are not allowed to use it.")
            logger.warning("Blocked unauthorized user: %s", sender_id)
            return
        return await func(event)

    return wrapper


START_TEXT = """
👩‍💻 **PhotoStiller Bot**

I can download images in two ways:

1) Send me a direct image URL and I will download it.
2) Send a Telegram channel link and use `/download <channel_link>` to download all photos from that channel history.

Commands:
/start - welcome message
/download <channel_link> - download all channel photos
/status - show current progress
/cancel - stop current download
/pause - pause or resume current download
/help - usage instructions

Channel link formats:
https://t.me/SomeChannel
t.me/SomeChannel
@SomeChannel
private invite link if the bot already has access

Important: for channel history downloads, add this bot as an admin to the channel first.
""".strip()


HELP_TEXT = """
**How to use**

Direct image:
Send any direct image URL like:
`https://site.com/image.jpg`

Channel images:
`/download https://t.me/SomeChannel`

The bot will scan message history, download photos only, create a ZIP, send it to you, then clean server storage.

Use:
/status - progress
/cancel - stop
/pause - pause/resume

Only the OWNER_ID from Render env can use this bot.
""".strip()


@client.on(events.NewMessage(pattern=r"^/start$"))
@owner_only
async def start_handler(event):
    await event.reply(START_TEXT, buttons=[[Button.url("Open BotFather", "https://t.me/BotFather")]], parse_mode="md")


@client.on(events.NewMessage(pattern=r"^/help$"))
@owner_only
async def help_handler(event):
    await event.reply(HELP_TEXT, parse_mode="md")


@client.on(events.NewMessage(pattern=r"^/status$"))
@owner_only
async def status_handler(event):
    await event.reply(downloader.status_text(event.sender_id))


@client.on(events.NewMessage(pattern=r"^/cancel$"))
@owner_only
async def cancel_handler(event):
    text = await downloader.cancel(event.sender_id)
    await event.reply(text)


@client.on(events.NewMessage(pattern=r"^/pause$"))
@owner_only
async def pause_handler(event):
    text = await downloader.toggle_pause(event.sender_id)
    await event.reply(text)


@client.on(events.NewMessage(pattern=r"^/download(?:\s+(.+))?$"))
@owner_only
async def download_handler(event):
    channel_link = event.pattern_match.group(1)
    if not channel_link:
        await event.reply("Usage: `/download https://t.me/SomeChannel`", parse_mode="md")
        return

    channel_link = normalize_channel_input(channel_link)
    if not is_telegram_channel_link(channel_link):
        await event.reply("❌ Invalid channel link. Use https://t.me/channel, t.me/channel, or @channel")
        return

    await event.reply(
        f"Channel detected:\n`{channel_link}`\n\nPress Start Download to begin.",
        buttons=[
            [make_start_button(channel_link)],
            [Button.inline("Cancel", data=b"cancel")],
        ],
        parse_mode="md",
    )


@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != OWNER_ID:
        await event.answer("Not allowed", alert=True)
        return

    data = event.data.decode("utf-8", errors="ignore")

    if data.startswith("start:"):
        key = data.split(":", 1)[1]
        channel_link = pending_channel_links.pop(key, None)
        if not channel_link:
            await event.answer("This download button expired. Send the channel link again.", alert=True)
            return
        await event.answer("Starting...")
        text = await downloader.start_download(event.sender_id, channel_link)
        await event.edit(text)
        return

    if data == "cancel":
        await event.answer("Cancelling...")
        text = await downloader.cancel(event.sender_id)
        await event.respond(text)
        return

    if data == "pause":
        await event.answer("Updating...")
        text = await downloader.toggle_pause(event.sender_id)
        await event.respond(text)
        return


@client.on(events.NewMessage)
@owner_only
async def text_handler(event):
    text = event.raw_text or ""
    if text.startswith("/"):
        return

    urls = extract_urls(text)
    if not urls:
        await event.reply("Send a direct image URL or use `/download <channel_link>`.", parse_mode="md")
        return

    # If the user sends a Telegram channel link without /download, offer a button.
    channel_links = [normalize_channel_input(url) for url in urls if is_telegram_channel_link(normalize_channel_input(url))]
    if channel_links:
        channel_link = channel_links[0]
        await event.reply(
            f"Telegram channel link detected:\n`{channel_link}`\n\nPress Start Download to download all photos.",
            buttons=[
                [make_start_button(channel_link)],
                [Button.inline("Cancel", data=b"cancel")],
            ],
            parse_mode="md",
        )
        return

    selected = urls[:MAX_URLS_PER_MESSAGE]
    temp_folder = DOWNLOAD_ROOT / "direct_urls" / str(event.sender_id)
    temp_folder.mkdir(parents=True, exist_ok=True)
    downloaded_paths = []

    await event.reply(f"Found {len(selected)} URL(s). Downloading...")

    for url in selected:
        if not is_valid_http_url(url):
            await event.reply(f"❌ Invalid URL:\n{url}")
            continue

        try:
            image_url = url
            try:
                image_path = await download_direct_image(image_url, temp_folder)
            except Exception:
                og_image = await extract_og_image(url)
                if not og_image:
                    raise
                image_url = og_image
                image_path = await download_direct_image(image_url, temp_folder)

            downloaded_paths.append(image_path)
            await client.send_file(event.chat_id, str(image_path), caption="✅ Downloaded image")
        except Exception as exc:
            await event.reply(f"❌ Failed to download:\n{url}\n\nReason: {exc}")
            logger.warning("Direct URL download failed for %s: %s", url, exc)

    cleanup_paths(downloaded_paths)


async def main():
    check_config()
    ensure_dirs()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    threading.Thread(target=run_health_server, daemon=True).start()

    logger.info("Starting PhotoStiller Telethon bot...")
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    logger.info("Bot started as @%s", getattr(me, "username", "unknown"))
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
    except RPCError as exc:
        logger.exception("Telegram RPC error: %s", exc)
        raise
