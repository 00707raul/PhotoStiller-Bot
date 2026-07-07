import asyncio
import secrets
import threading

from flask import Flask
from telethon import Button, TelegramClient, events
from telethon.errors import RPCError
from telethon.sessions import StringSession

from channel_downloader import ChannelImageDownloader
from config import (
    API_HASH,
    API_ID,
    BOT_TOKEN,
    DATA_DIR,
    DOWNLOAD_ROOT,
    MAX_URLS_PER_MESSAGE,
    OWNER_ID,
    PUBLIC_MODE,
    ALLOW_PRIVATE_LINKS_FOR_PUBLIC,
    MAX_ACTIVE_JOBS,
    PORT,
    SESSION_NAME,
    STRING_SESSION,
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
    is_web_telegram_url,
    normalize_channel_input,
)

logger = setup_logging()
db = DownloadDB()

# Bot client: talks with you in @PhotoStiller_bot.
bot_client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Reader client: reads channel history.
# If STRING_SESSION exists, it uses your Telegram user account, which can open private invite links.
# If not, it falls back to the bot account, which works only where Telegram allows bot accounts.
uses_user_session = bool(STRING_SESSION)
reader_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH) if uses_user_session else bot_client

downloader = ChannelImageDownloader(bot_client, reader_client, db, logger, uses_user_session=uses_user_session)
pending_channel_links = {}


health_app = Flask(__name__)


@health_app.route("/")
def index():
    return "PhotoStiller bot is running."


@health_app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "PhotoStiller",
        "reader": "user_session" if uses_user_session else "bot_session",
    }


def run_health_server():
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def make_start_button(channel_link: str, user_id: int):
    key = secrets.token_urlsafe(8)
    pending_channel_links[key] = {"link": channel_link, "user_id": user_id}
    return Button.inline("Start Download", data=f"start:{key}".encode("utf-8"))


def check_config() -> None:
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not PUBLIC_MODE and not OWNER_ID:
        missing.append("OWNER_ID")
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID and user_id == OWNER_ID)


def is_private_invite(channel_link: str) -> bool:
    return "+" in channel_link or "/joinchat/" in channel_link


def is_allowed_user(user_id: int) -> bool:
    return PUBLIC_MODE or is_owner(user_id)


def access_control(func):
    async def wrapper(event):
        sender_id = event.sender_id
        if not is_allowed_user(sender_id):
            await event.reply("❌ This bot is private. You are not allowed to use it.")
            logger.warning("Blocked unauthorized user: %s", sender_id)
            return
        return await func(event)

    return wrapper


def owner_only(func):
    async def wrapper(event):
        sender_id = event.sender_id
        if not is_owner(sender_id):
            await event.reply("❌ This command is only available to the bot owner.")
            logger.warning("Blocked non-owner from owner-only command: %s", sender_id)
            return
        return await func(event)

    return wrapper


START_TEXT = """
👩‍💻 **PhotoStiller Bot**

I can download images in two ways:

1) Send me a direct image URL and I will download it.
2) Send a Telegram channel link and use `/download <channel_link>` to download all photos from channel history.

Commands:
/start - welcome message
/download <channel_link> - download all channel photos
/status - show current progress
/cancel - stop current download
/pause - pause or resume current download
/cleanup - delete temporary downloaded files
/sessionstatus - check if private-link mode is active
/help - usage instructions

Channel link formats:
https://t.me/SomeChannel
t.me/SomeChannel
@SomeChannel
https://t.me/+PRIVATE_INVITE_LINK
https://t.me/joinchat/PRIVATE_INVITE_HASH

Do not send web.telegram.org browser links. Use a real t.me link or @username.
Private invite links need STRING_SESSION in Render env.
""".strip()


HELP_TEXT = """
**How to use**

Direct image:
Send any direct image URL like:
`https://site.com/image.jpg`

Channel images:
`/download https://t.me/SomeChannel`

Private invite links:
`/download https://t.me/+XXXX`

For private invite links, the bot must have `STRING_SESSION` in Render env. This lets your own Telegram user account read/join the channel while the bot sends the ZIP back to you.

Important: `web.telegram.org/...` links are browser-internal links and cannot be downloaded. Copy the real `https://t.me/...` link or send `@channelusername`.

Use:
/status - progress
/cancel - stop
/pause - pause/resume
/cleanup - clear temporary server downloads
/sessionstatus - check reader mode

If PUBLIC_MODE=true, anyone can use public-channel and direct-image features. Private invite links are owner-only unless ALLOW_PRIVATE_LINKS_FOR_PUBLIC=true.
""".strip()


@bot_client.on(events.NewMessage(pattern=r"^/start$"))
@access_control
async def start_handler(event):
    await event.reply(START_TEXT, buttons=[[Button.url("Open BotFather", "https://t.me/BotFather")]], parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/help$"))
@access_control
async def help_handler(event):
    await event.reply(HELP_TEXT, parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/sessionstatus$"))
@owner_only
async def session_status_handler(event):
    if uses_user_session:
        await event.reply("✅ STRING_SESSION is active. Private invite links can work if your account has access.")
    else:
        await event.reply(
            "⚠️ STRING_SESSION is missing. Public links may work, but private invite links like `https://t.me/+XXXX` will not work.",
            parse_mode="md",
        )


@bot_client.on(events.NewMessage(pattern=r"^/status$"))
@access_control
async def status_handler(event):
    await event.reply(downloader.status_text(event.sender_id))


@bot_client.on(events.NewMessage(pattern=r"^/cancel$"))
@access_control
async def cancel_handler(event):
    text = await downloader.cancel(event.sender_id)
    await event.reply(text)


@bot_client.on(events.NewMessage(pattern=r"^/pause$"))
@access_control
async def pause_handler(event):
    text = await downloader.toggle_pause(event.sender_id)
    await event.reply(text)


@bot_client.on(events.NewMessage(pattern=r"^/cleanup$"))
@owner_only
async def cleanup_handler(event):
    active_job = downloader.get_job(event.sender_id)
    if active_job and active_job.status in {"validating", "running", "paused", "delivering"}:
        await event.reply("❌ A download is active. Use /cancel first, then /cleanup.")
        return

    cleanup_paths([DOWNLOAD_ROOT])
    ensure_dirs()
    await event.reply("✅ Cleanup complete. Temporary download files were deleted.")


@bot_client.on(events.NewMessage(pattern=r"^/download(?:\s+(.+))?$"))
@access_control
async def download_handler(event):
    channel_link = event.pattern_match.group(1)
    if not channel_link:
        await event.reply("Usage: `/download https://t.me/SomeChannel`", parse_mode="md")
        return

    channel_link = normalize_channel_input(channel_link)
    if is_web_telegram_url(channel_link):
        await event.reply("❌ This is a web.telegram.org browser link. It is not a real channel link. Open the channel → Share/Copy Link and send `https://t.me/...` or `@channelusername`.", parse_mode="md")
        return

    if not is_telegram_channel_link(channel_link):
        await event.reply("❌ Invalid channel link. Use https://t.me/channel, t.me/+invite, t.me/joinchat/hash, or @channel")
        return

    private_note = ""
    if is_private_invite(channel_link):
        if PUBLIC_MODE and not is_owner(event.sender_id) and not ALLOW_PRIVATE_LINKS_FOR_PUBLIC:
            await event.reply(
                "❌ Private invite links are disabled for public users. Send a public channel link, or ask the owner to enable ALLOW_PRIVATE_LINKS_FOR_PUBLIC.",
            )
            return
        private_note = "\n\nPrivate invite link detected. It needs STRING_SESSION active. Check with /sessionstatus."

    await event.reply(
        f"Channel detected:\n`{channel_link}`{private_note}\n\nPress Start Download to begin.",
        buttons=[
            [make_start_button(channel_link, event.sender_id)],
            [Button.inline("Cancel", data=b"cancel")],
        ],
        parse_mode="md",
    )


@bot_client.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_allowed_user(event.sender_id):
        await event.answer("Not allowed", alert=True)
        return

    data = event.data.decode("utf-8", errors="ignore")

    if data.startswith("start:"):
        key = data.split(":", 1)[1]
        pending = pending_channel_links.pop(key, None)
        if not pending:
            await event.answer("This download button expired. Send the channel link again.", alert=True)
            return
        if pending.get("user_id") != event.sender_id:
            await event.answer("This button belongs to another user.", alert=True)
            return
        channel_link = pending["link"]
        if is_private_invite(channel_link) and PUBLIC_MODE and not is_owner(event.sender_id) and not ALLOW_PRIVATE_LINKS_FOR_PUBLIC:
            await event.answer("Private invite links are disabled for public users.", alert=True)
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


@bot_client.on(events.NewMessage)
@access_control
async def text_handler(event):
    text = event.raw_text or ""
    if text.startswith("/"):
        return

    urls = extract_urls(text)
    if urls and any(is_web_telegram_url(url) for url in urls):
        await event.reply("❌ `web.telegram.org/...` links are only browser links. I cannot read channel history from that. Send a real Telegram link like `https://t.me/channelname`, `https://t.me/+invite`, or `@channelname`.", parse_mode="md")
        return

    if not urls:
        await event.reply("Send a direct image URL or use `/download <channel_link>`.", parse_mode="md")
        return

    # If the user sends a Telegram channel link without /download, offer a button.
    channel_links = [normalize_channel_input(url) for url in urls if is_telegram_channel_link(normalize_channel_input(url))]
    if channel_links:
        channel_link = channel_links[0]
        private_note = ""
        if is_private_invite(channel_link):
            if PUBLIC_MODE and not is_owner(event.sender_id) and not ALLOW_PRIVATE_LINKS_FOR_PUBLIC:
                await event.reply(
                    "❌ Private invite links are disabled for public users. Send a public channel link instead.",
                )
                return
            private_note = "\n\nPrivate invite link detected. It needs STRING_SESSION active."
        await event.reply(
            f"Telegram channel link detected:\n`{channel_link}`{private_note}\n\nPress Start Download to download all photos.",
            buttons=[
                [make_start_button(channel_link, event.sender_id)],
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
            await bot_client.send_file(event.chat_id, str(image_path), caption="✅ Downloaded image")
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
    await bot_client.start(bot_token=BOT_TOKEN)
    bot_me = await bot_client.get_me()
    logger.info("Bot started as @%s", getattr(bot_me, "username", "unknown"))
    logger.info("PUBLIC_MODE=%s | MAX_ACTIVE_JOBS=%s | private_links_for_public=%s", PUBLIC_MODE, MAX_ACTIVE_JOBS, ALLOW_PRIVATE_LINKS_FOR_PUBLIC)

    if uses_user_session:
        await reader_client.start()
        user_me = await reader_client.get_me()
        logger.info(
            "Reader user session started as @%s / %s",
            getattr(user_me, "username", None) or "no_username",
            getattr(user_me, "id", "unknown"),
        )
    else:
        logger.warning("STRING_SESSION missing. Private invite links will not work.")

    await bot_client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
    except RPCError as exc:
        logger.exception("Telegram RPC error: %s", exc)
        raise
