import asyncio
import secrets
import threading
import time
from pathlib import Path

from flask import Flask
from telethon import Button, TelegramClient, events
from telethon.errors import RPCError
from telethon.sessions import StringSession

from channel_downloader import ChannelImageDownloader
from config import (
    ADMIN_IDS,
    ALLOW_PRIVATE_LINKS_FOR_PUBLIC,
    API_HASH,
    API_ID,
    BOT_NAME,
    BOT_TOKEN,
    DATA_DIR,
    DOWNLOAD_ROOT,
    LOG_FILE,
    MAX_ACTIVE_JOBS,
    MAX_URLS_PER_MESSAGE,
    MIN_SECONDS_BETWEEN_JOBS,
    OWNER_ID,
    OWNER_MAX_PHOTOS_PER_JOB,
    PORT,
    PUBLIC_MAX_PHOTOS_PER_JOB,
    PUBLIC_MODE,
    SESSION_NAME,
    STRING_SESSION,
    USER_DAILY_CHANNEL_LIMIT,
    USER_DAILY_DIRECT_LIMIT,
)
from database import DownloadDB
from logger_setup import setup_logging
from utils import (
    cleanup_paths,
    disk_report,
    download_direct_image,
    ensure_dirs,
    extract_og_image,
    extract_urls,
    format_bytes,
    folder_size_bytes,
    is_telegram_channel_link,
    is_valid_http_url,
    is_web_telegram_url,
    normalize_channel_input,
)

logger = setup_logging()
db = DownloadDB()
STARTED_AT = time.time()
last_channel_start: dict[int, float] = {}
pending_channel_links: dict[str, dict] = {}

# Bot client: receives commands and sends files.
bot_client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Reader client: reads channel history. STRING_SESSION allows private invite links.
uses_user_session = bool(STRING_SESSION)
reader_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH) if uses_user_session else bot_client

downloader = ChannelImageDownloader(bot_client, reader_client, db, logger, uses_user_session=uses_user_session)

health_app = Flask(__name__)


@health_app.route("/")
def index():
    return f"{BOT_NAME} bot is running."


@health_app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": BOT_NAME,
        "reader": "user_session" if uses_user_session else "bot_session",
        "public_mode": get_public_mode(),
        "active_jobs": len(downloader.active_jobs()),
    }


def run_health_server():
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def uptime_text() -> str:
    seconds = int(time.time() - STARTED_AT)
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    days, hrs = divmod(hrs, 24)
    if days:
        return f"{days}d {hrs}h {mins}m"
    if hrs:
        return f"{hrs}h {mins}m"
    return f"{mins}m {sec}s"


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID and user_id == OWNER_ID)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_public_mode() -> bool:
    value = db.get_setting("PUBLIC_MODE")
    if value is None:
        return PUBLIC_MODE
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_private_invite(channel_link: str) -> bool:
    return "+" in channel_link or "/joinchat/" in channel_link


def is_allowed_user(user_id: int) -> bool:
    return get_public_mode() or is_admin(user_id)


async def touch_user(event) -> None:
    try:
        sender = await event.get_sender()
        db.touch_user(
            event.sender_id,
            getattr(sender, "username", "") or "",
            getattr(sender, "first_name", "") or "",
            getattr(sender, "last_name", "") or "",
        )
    except Exception:
        db.touch_user(event.sender_id)


def access_control(func):
    async def wrapper(event):
        await touch_user(event)
        sender_id = event.sender_id
        if db.is_banned(sender_id):
            await event.reply("❌ You are blocked from using this bot.")
            logger.warning("Blocked banned user: %s", sender_id)
            return
        if not is_allowed_user(sender_id):
            await event.reply("❌ This bot is private right now. Send /whoami to get your user ID and ask the owner for access.")
            logger.warning("Blocked unauthorized user: %s", sender_id)
            return
        return await func(event)

    return wrapper


def admin_only(func):
    async def wrapper(event):
        await touch_user(event)
        sender_id = event.sender_id
        if not is_admin(sender_id):
            await event.reply("❌ This command is only available to the bot owner/admins.")
            logger.warning("Blocked non-admin from admin command: %s", sender_id)
            return
        return await func(event)

    return wrapper


def make_start_button(channel_link: str, user_id: int):
    key = secrets.token_urlsafe(8)
    pending_channel_links[key] = {"link": channel_link, "user_id": user_id, "created_at": time.time()}
    return Button.inline("▶️ Start Download", data=f"start:{key}".encode("utf-8"))


def check_config() -> None:
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not get_public_mode() and not OWNER_ID:
        missing.append("OWNER_ID")
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")


def max_photos_for_user(user_id: int) -> int:
    if is_admin(user_id):
        return OWNER_MAX_PHOTOS_PER_JOB
    return PUBLIC_MAX_PHOTOS_PER_JOB


def quota_text(user_id: int) -> str:
    direct, channel, images = db.get_usage_today(user_id)
    return (
        f"Today usage:\n"
        f"Direct URLs: {direct}/{USER_DAILY_DIRECT_LIMIT}\n"
        f"Channel jobs: {channel}/{USER_DAILY_CHANNEL_LIMIT}\n"
        f"Images delivered: {images}\n"
        f"Public channel photo cap: {PUBLIC_MAX_PHOTOS_PER_JOB if PUBLIC_MAX_PHOTOS_PER_JOB else 'unlimited'}"
    )


def can_start_channel_job(user_id: int) -> tuple[bool, str]:
    if is_admin(user_id):
        return True, ""
    _, channel_count, _ = db.get_usage_today(user_id)
    if channel_count >= USER_DAILY_CHANNEL_LIMIT:
        return False, f"Daily channel download limit reached: {channel_count}/{USER_DAILY_CHANNEL_LIMIT}. Try again tomorrow."
    last = last_channel_start.get(user_id, 0)
    remaining = int(MIN_SECONDS_BETWEEN_JOBS - (time.time() - last))
    if remaining > 0:
        return False, f"Please wait {remaining}s before starting another download."
    return True, ""


START_TEXT = f"""
📥 **{BOT_NAME}**

I can save images in two ways:

1) Send me a direct image URL.
2) Send a Telegram channel link or use `/download <channel_link>` to download accessible photos from channel history.

Commands:
/start - welcome message
/help - usage instructions
/whoami - show your Telegram user ID
/limits - show your limits
/download <channel_link> - download channel photos
/status - show your current progress
/pause - pause/resume your download
/cancel - stop your download
/cleanup - owner cleanup

Telegram links must look like:
`https://t.me/SomeChannel`
`t.me/SomeChannel`
`@SomeChannel`
`https://t.me/+PRIVATE_INVITE_LINK`

Do not send `web.telegram.org/...` browser links. Use a real `t.me` link.
""".strip()

HELP_TEXT = f"""
**How to use {BOT_NAME}**

Direct image:
Send: `https://site.com/image.jpg`

Telegram channel:
`/download https://t.me/SomeChannel`

Private invite links:
`/download https://t.me/+XXXX`

Private links need `STRING_SESSION` in Render env. Public users cannot use private links unless the owner enables it.

Useful commands:
/status - progress
/pause - pause/resume
/cancel - stop
/limits - show daily limits
/whoami - show your user ID

Admin commands:
/admin - control panel
/stats - bot statistics
/queue - active downloads
/users - recent users
/ban <id> - block user
/unban <id> - unblock user
/banlist - blocked users
/publicmode on|off|status - change public mode without Render
/broadcast <message> - message all users
/logs [lines] - send recent logs
/cleanup - delete temporary server files
/cancelall - stop every active job

Locked/paid Telegram media cannot be bypassed. The bot downloads only accessible photos.
""".strip()


@bot_client.on(events.NewMessage(pattern=r"^/whoami$"))
async def whoami_handler(event):
    await touch_user(event)
    await event.reply(f"Your Telegram numeric ID is:\n`{event.sender_id}`", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/start$"))
@access_control
async def start_handler(event):
    await event.reply(
        START_TEXT,
        buttons=[
            [Button.inline("📊 My status", data=b"my_status"), Button.inline("📘 Help", data=b"help")],
        ],
        parse_mode="md",
    )


@bot_client.on(events.NewMessage(pattern=r"^/help$"))
@access_control
async def help_handler(event):
    await event.reply(HELP_TEXT, parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/limits$"))
@access_control
async def limits_handler(event):
    await event.reply(quota_text(event.sender_id))


@bot_client.on(events.NewMessage(pattern=r"^/ping$"))
@access_control
async def ping_handler(event):
    await event.reply(f"✅ Pong. Uptime: {uptime_text()}")


@bot_client.on(events.NewMessage(pattern=r"^/sessionstatus$"))
@admin_only
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
    await event.reply(downloader.status_text(event.sender_id), parse_mode="md")


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
@admin_only
async def cleanup_handler(event):
    active_job = downloader.get_job(event.sender_id)
    if active_job and active_job.status in {"validating", "scanning", "running", "paused", "zipping", "delivering"}:
        await event.reply("❌ A download is active. Use /cancel first, then /cleanup.")
        return
    cleanup_paths([DOWNLOAD_ROOT])
    ensure_dirs()
    await event.reply("✅ Cleanup complete. Temporary download files were deleted.\n\n" + disk_report())


@bot_client.on(events.NewMessage(pattern=r"^/admin$"))
@admin_only
async def admin_handler(event):
    await event.reply(
        f"🛠 **{BOT_NAME} Admin Panel**\nChoose an action:",
        buttons=[
            [Button.inline("📊 Stats", data=b"admin_stats"), Button.inline("📥 Queue", data=b"admin_queue")],
            [Button.inline("👥 Users", data=b"admin_users"), Button.inline("💾 Disk", data=b"admin_disk")],
            [Button.inline("🌍 Public mode", data=b"admin_public"), Button.inline("🧹 Cleanup", data=b"admin_cleanup")],
        ],
        parse_mode="md",
    )


@bot_client.on(events.NewMessage(pattern=r"^/stats$"))
@admin_only
async def stats_handler(event):
    direct, channels, images = db.usage_totals_today()
    text = (
        f"📊 **{BOT_NAME} stats**\n"
        f"Uptime: {uptime_text()}\n"
        f"Public mode: {get_public_mode()}\n"
        f"Reader: {'user session' if uses_user_session else 'bot session'}\n"
        f"Active jobs: {len(downloader.active_jobs())}/{MAX_ACTIVE_JOBS}\n"
        f"Known users: {db.user_count()}\n"
        f"Today direct URLs: {direct}\n"
        f"Today channel jobs: {channels}\n"
        f"Today images delivered: {images}\n\n"
        f"{disk_report()}"
    )
    await event.reply(text, parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/queue$"))
@admin_only
async def queue_handler(event):
    await event.reply(downloader.queue_text(), parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/users(?:\s+(\d+))?$"))
@admin_only
async def users_handler(event):
    limit = int(event.pattern_match.group(1) or 15)
    limit = max(1, min(limit, 50))
    rows = db.list_users(limit)
    if not rows:
        await event.reply("No users saved yet.")
        return
    lines = [f"Recent users ({len(rows)}):"]
    for user_id, username, first_name, banned, first_seen, last_seen in rows:
        name = f"@{username}" if username else (first_name or "no name")
        ban = " 🚫" if banned else ""
        lines.append(f"• `{user_id}` {name}{ban} | last: {last_seen}")
    await event.reply("\n".join(lines), parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/ban\s+(\d+)$"))
@admin_only
async def ban_handler(event):
    user_id = int(event.pattern_match.group(1))
    if is_admin(user_id):
        await event.reply("❌ You cannot ban the owner/admin.")
        return
    db.set_banned(user_id, True)
    await event.reply(f"✅ User `{user_id}` banned.", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/unban\s+(\d+)$"))
@admin_only
async def unban_handler(event):
    user_id = int(event.pattern_match.group(1))
    db.set_banned(user_id, False)
    await event.reply(f"✅ User `{user_id}` unbanned.", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/banlist$"))
@admin_only
async def banlist_handler(event):
    rows = db.list_banned()
    if not rows:
        await event.reply("Ban list is empty.")
        return
    lines = ["Banned users:"]
    for user_id, username, first_name, last_seen in rows:
        name = f"@{username}" if username else (first_name or "no name")
        lines.append(f"• `{user_id}` {name} | last: {last_seen}")
    await event.reply("\n".join(lines), parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/publicmode(?:\s+(on|off|status))?$"))
@admin_only
async def publicmode_handler(event):
    arg = event.pattern_match.group(1)
    if not arg or arg == "status":
        await event.reply(f"Public mode is currently: `{get_public_mode()}`", parse_mode="md")
        return
    db.set_setting("PUBLIC_MODE", "true" if arg == "on" else "false")
    await event.reply(f"✅ Public mode changed to `{arg}`. This change is saved in SQLite and does not need Render redeploy.", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/broadcast\s+([\s\S]+)$"))
@admin_only
async def broadcast_handler(event):
    message = event.pattern_match.group(1).strip()
    if not message:
        await event.reply("Usage: /broadcast your message")
        return
    rows = db.list_users(1000)
    sent = 0
    failed = 0
    for user_id, *_ in rows:
        if db.is_banned(user_id):
            continue
        try:
            await bot_client.send_message(user_id, message)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await event.reply(f"✅ Broadcast finished. Sent: {sent}, failed: {failed}.")


@bot_client.on(events.NewMessage(pattern=r"^/logs(?:\s+(\d+))?$"))
@admin_only
async def logs_handler(event):
    lines_count = int(event.pattern_match.group(1) or 80)
    lines_count = max(10, min(lines_count, 300))
    path = Path(LOG_FILE)
    if not path.exists():
        await event.reply("No log file yet.")
        return
    lines = path.read_text(errors="ignore").splitlines()[-lines_count:]
    text = "\n".join(lines) or "Log is empty."
    if len(text) > 3500:
        await bot_client.send_file(event.chat_id, str(path), caption="bot.log")
    else:
        await event.reply(f"```\n{text}\n```", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/cancelall$"))
@admin_only
async def cancelall_handler(event):
    count = await downloader.cancel_all()
    await event.reply(f"Cancelled active jobs: {count}")


@bot_client.on(events.NewMessage(pattern=r"^/download(?:\s+(.+))?$"))
@access_control
async def download_handler(event):
    channel_link = event.pattern_match.group(1)
    if not channel_link:
        await event.reply("Usage: `/download https://t.me/SomeChannel`", parse_mode="md")
        return

    channel_link = normalize_channel_input(channel_link)
    await offer_channel_download(event, channel_link)


async def offer_channel_download(event, channel_link: str):
    if is_web_telegram_url(channel_link):
        await event.reply("❌ This is a web.telegram.org browser link. Open the channel → Share/Copy Link and send `https://t.me/...` or `@channelusername`.", parse_mode="md")
        return

    if not is_telegram_channel_link(channel_link):
        await event.reply("❌ Invalid channel link. Use https://t.me/channel, t.me/+invite, t.me/joinchat/hash, or @channel")
        return

    if is_private_invite(channel_link):
        if get_public_mode() and not is_admin(event.sender_id) and not ALLOW_PRIVATE_LINKS_FOR_PUBLIC:
            await event.reply("❌ Private invite links are disabled for public users. Send a public channel link instead.")
            return
        private_note = "\n\n🔐 Private invite link detected. It needs STRING_SESSION active."
    else:
        private_note = ""

    allowed, reason = can_start_channel_job(event.sender_id)
    if not allowed:
        await event.reply(f"❌ {reason}")
        return

    cap = max_photos_for_user(event.sender_id)
    cap_note = f"\nPhoto cap for this job: **{cap}**" if cap else "\nPhoto cap for this job: **unlimited**"
    await event.reply(
        f"Channel detected:\n`{channel_link}`{private_note}{cap_note}\n\nPress Start Download to begin.",
        buttons=[
            [make_start_button(channel_link, event.sender_id)],
            [Button.inline("Cancel", data=b"cancel")],
        ],
        parse_mode="md",
    )


@bot_client.on(events.CallbackQuery)
async def callback_handler(event):
    await touch_user(event)
    if db.is_banned(event.sender_id):
        await event.answer("Blocked", alert=True)
        return
    if not is_allowed_user(event.sender_id):
        await event.answer("Not allowed", alert=True)
        return

    data = event.data.decode("utf-8", errors="ignore")

    if data == "help":
        await event.answer("Help")
        await event.respond(HELP_TEXT, parse_mode="md")
        return

    if data == "my_status":
        await event.answer("Status")
        await event.respond(downloader.status_text(event.sender_id) + "\n\n" + quota_text(event.sender_id), parse_mode="md")
        return

    if data.startswith("admin_"):
        if not is_admin(event.sender_id):
            await event.answer("Admin only", alert=True)
            return
        await event.answer("OK")
        if data == "admin_stats":
            direct, channels, images = db.usage_totals_today()
            await event.respond(f"Stats:\nUptime: {uptime_text()}\nUsers: {db.user_count()}\nToday direct: {direct}\nToday channels: {channels}\nToday images: {images}\nActive jobs: {len(downloader.active_jobs())}/{MAX_ACTIVE_JOBS}")
        elif data == "admin_queue":
            await event.respond(downloader.queue_text(), parse_mode="md")
        elif data == "admin_users":
            rows = db.list_users(10)
            text = "Recent users:\n" + "\n".join([f"• {r[0]} @{r[1] or '-'}" for r in rows]) if rows else "No users yet."
            await event.respond(text)
        elif data == "admin_disk":
            await event.respond(disk_report())
        elif data == "admin_public":
            await event.respond(f"Public mode: {get_public_mode()}\nUse /publicmode on or /publicmode off to change it.")
        elif data == "admin_cleanup":
            cleanup_paths([DOWNLOAD_ROOT])
            ensure_dirs()
            await event.respond("Cleanup complete.\n" + disk_report())
        return

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
        if is_private_invite(channel_link) and get_public_mode() and not is_admin(event.sender_id) and not ALLOW_PRIVATE_LINKS_FOR_PUBLIC:
            await event.answer("Private invite links are disabled for public users.", alert=True)
            return

        allowed, reason = can_start_channel_job(event.sender_id)
        if not allowed:
            await event.answer(reason, alert=True)
            return

        await event.answer("Starting...")
        last_channel_start[event.sender_id] = time.time()
        db.add_usage(event.sender_id, channel=1)
        db.log_event(event.sender_id, "channel_download_start", channel_link)
        text = await downloader.start_download(event.sender_id, channel_link, max_photos=max_photos_for_user(event.sender_id))
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
        await event.reply("❌ `web.telegram.org/...` links are browser links. Send a real Telegram link like `https://t.me/channelname`, `https://t.me/+invite`, or `@channelname`.", parse_mode="md")
        return

    if not urls:
        await event.reply("Send a direct image URL or use `/download <channel_link>`.", parse_mode="md")
        return

    channel_links = [normalize_channel_input(url) for url in urls if is_telegram_channel_link(normalize_channel_input(url))]
    if channel_links:
        await offer_channel_download(event, channel_links[0])
        return

    direct_count, _, _ = db.get_usage_today(event.sender_id)
    if not is_admin(event.sender_id) and direct_count >= USER_DAILY_DIRECT_LIMIT:
        await event.reply(f"❌ Daily direct URL limit reached: {direct_count}/{USER_DAILY_DIRECT_LIMIT}. Try again tomorrow.")
        return

    selected = urls[:MAX_URLS_PER_MESSAGE]
    temp_folder = DOWNLOAD_ROOT / "direct_urls" / str(event.sender_id)
    temp_folder.mkdir(parents=True, exist_ok=True)
    downloaded_paths = []

    status_msg = await event.reply(f"Found {len(selected)} URL(s). Downloading...")

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
            db.add_usage(event.sender_id, direct=1, images=1)
            db.log_event(event.sender_id, "direct_url", url)
        except Exception as exc:
            await event.reply(f"❌ Failed to download:\n{url}\n\nReason: {exc}")
            logger.warning("Direct URL download failed for %s: %s", url, exc)

    cleanup_paths(downloaded_paths)
    try:
        await status_msg.delete()
    except Exception:
        pass


async def main():
    check_config()
    ensure_dirs()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    threading.Thread(target=run_health_server, daemon=True).start()

    logger.info("Starting %s Telethon bot...", BOT_NAME)
    await bot_client.start(bot_token=BOT_TOKEN)
    bot_me = await bot_client.get_me()
    logger.info("Bot started as @%s", getattr(bot_me, "username", "unknown"))
    logger.info("PUBLIC_MODE=%s | MAX_ACTIVE_JOBS=%s | private_links_for_public=%s", get_public_mode(), MAX_ACTIVE_JOBS, ALLOW_PRIVATE_LINKS_FOR_PUBLIC)

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
