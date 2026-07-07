import ipaddress
import os
import re
import socket
import tempfile
import threading
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv
from flask import Flask
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "10"))
MAX_IMAGE_SIZE = MAX_IMAGE_MB * 1024 * 1024
MAX_URLS_PER_MESSAGE = int(os.getenv("MAX_URLS_PER_MESSAGE", "5"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))
PORT = int(os.getenv("PORT", "10000"))

URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)

health_app = Flask(__name__)


@health_app.get("/")
def home():
    return "PhotoStiller bot is running in polling mode."


@health_app.get("/health")
def health():
    return {"ok": True, "mode": "polling"}


def run_health_server() -> None:
    """Small HTTP server for Render/Koyeb health checks. This is NOT a Telegram webhook."""
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def extract_urls(text: str) -> list[str]:
    urls = URL_RE.findall(text or "")
    # Remove common trailing punctuation copied from messages
    cleaned = [u.rstrip(".,;!?)"] for u in urls]
    # Keep order but remove duplicates
    return list(dict.fromkeys(cleaned))[:MAX_URLS_PER_MESSAGE]


def is_public_hostname(hostname: str) -> bool:
    """Block localhost/private IPs so the bot cannot be abused to access internal services."""
    if not hostname:
        return False

    try:
        addresses = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False

    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False

    return True


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid URL. Please send a normal http/https image link.")
    if not is_public_hostname(parsed.hostname or ""):
        raise ValueError("This URL is not allowed.")


def open_image_response(url: str) -> requests.Response:
    """Open image URL safely, following a few redirects manually."""
    current_url = url

    for _ in range(4):
        validate_url(current_url)

        response = requests.get(
            current_url,
            stream=True,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            headers={"User-Agent": "PhotoStillerBot/1.0"},
        )

        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Redirect without location header.")
            current_url = urljoin(current_url, location)
            continue

        response.raise_for_status()

        # Validate final URL too
        validate_url(response.url)
        return response

    raise ValueError("Too many redirects.")


def extension_from_content_type(content_type: str) -> str:
    subtype = content_type.split("/", 1)[-1].split(";", 1)[0].lower().strip()
    mapping = {
        "jpeg": "jpg",
        "jpg": "jpg",
        "png": "png",
        "webp": "webp",
        "gif": "gif",
        "bmp": "bmp",
    }
    return mapping.get(subtype, "img")


def download_image(url: str) -> tuple[str, str]:
    response = open_image_response(url)

    try:
        content_type = response.headers.get("Content-Type", "").lower()
        if not content_type.startswith("image/"):
            raise ValueError("This link is not a direct image link.")

        content_length = int(response.headers.get("Content-Length", "0") or 0)
        if content_length > MAX_IMAGE_SIZE:
            raise ValueError(f"Image is too large. Max size is {MAX_IMAGE_MB} MB.")

        extension = extension_from_content_type(content_type)

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}") as file:
            total = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_SIZE:
                    raise ValueError(f"Image is too large. Max size is {MAX_IMAGE_MB} MB.")
                file.write(chunk)
            return file.name, content_type
    finally:
        response.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "👩‍💻 Hi, I am PhotoStiller.\n\n"
        "Send me a direct image URL and I will download it for you.\n\n"
        "Example:\n"
        "https://example.com/image.jpg"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Send one or more direct image links.\n\n"
        f"Max image size: {MAX_IMAGE_MB} MB\n"
        f"Max links per message: {MAX_URLS_PER_MESSAGE}\n\n"
        "Supported: JPG, PNG, WEBP, GIF and other normal image URLs."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = message.text or ""
    urls = extract_urls(text)

    if not urls:
        await message.reply_text("Please send a direct image URL.")
        return

    for url in urls:
        temp_path = None
        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            temp_path, content_type = download_image(url)

            with open(temp_path, "rb") as image_file:
                # GIFs and very large files are better as documents; normal images as photos.
                if "gif" in content_type:
                    await message.reply_document(document=image_file, caption="Downloaded image ✅")
                else:
                    await message.reply_photo(photo=image_file, caption="Downloaded image ✅")

        except Exception as exc:
            await message.reply_text(f"❌ Failed to download:\n{url}\n\nReason: {exc}")
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it in environment variables.")

    # Start a simple HTTP health server so free web hosts can run this as a web service.
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("PhotoStiller bot is running with polling...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
