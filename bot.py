import asyncio
import html
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
from telegram import MessageEntity, Update
from telegram.constants import ChatAction
from telegram.error import TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "10"))
MAX_IMAGE_SIZE = MAX_IMAGE_MB * 1024 * 1024
MAX_URLS_PER_MESSAGE = int(os.getenv("MAX_URLS_PER_MESSAGE", "5"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))
PORT = int(os.getenv("PORT", "10000"))

# Keep parentheses inside URLs because many CDN links use things like format(webp).
URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
HTML_READ_LIMIT = 2 * 1024 * 1024

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


def clean_url(url: str) -> str:
    """Remove punctuation copied after a URL without breaking valid CDN URLs."""
    url = (url or "").strip()

    # Common sentence punctuation is almost never part of the actual URL.
    while url and url[-1] in ".,;!?":
        url = url[:-1]

    # Remove closing brackets only when they are extra wrappers around the URL.
    pairs = [("(", ")"), ("[", "]"), ("{", "}")]
    changed = True
    while changed:
        changed = False
        for opener, closer in pairs:
            if url.endswith(closer) and url.count(closer) > url.count(opener):
                url = url[:-1]
                changed = True

    return url


def extract_urls_from_message(message) -> list[str]:
    urls: list[str] = []
    text = message.text or message.caption or ""

    # Telegram already knows the exact URL entity, so use it first.
    try:
        entities = message.parse_entities(types=[MessageEntity.URL, MessageEntity.TEXT_LINK]) or {}
        for entity, value in entities.items():
            if entity.type == MessageEntity.URL:
                urls.append(value)
            elif entity.type == MessageEntity.TEXT_LINK and entity.url:
                urls.append(entity.url)
    except Exception:
        pass

    # Fallback regex for normal text.
    urls.extend(URL_RE.findall(text))

    cleaned = [clean_url(u) for u in urls]
    cleaned = [u for u in cleaned if u]

    # Keep order but remove duplicates.
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


def get_response(url: str, *, stream: bool) -> requests.Response:
    """Open URL safely, following a few redirects manually."""
    current_url = url

    for _ in range(5):
        validate_url(current_url)

        response = requests.get(
            current_url,
            stream=stream,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; PhotoStillerBot/1.0)",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )

        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Redirect without location header.")
            current_url = urljoin(current_url, location)
            continue

        response.raise_for_status()
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
        "svg+xml": "svg",
    }
    return mapping.get(subtype, "img")


def extract_meta_image(page_url: str, html_text: str) -> str | None:
    """Try to get image previews from normal web pages like Pinterest/share links."""
    patterns = [
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']image_src["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE)
        if match:
            image_url = html.unescape(match.group(1).strip())
            return urljoin(page_url, image_url)

    return None


def save_image_response(response: requests.Response) -> tuple[str, str]:
    try:
        content_type = response.headers.get("Content-Type", "").lower()
        if not content_type.startswith("image/"):
            raise ValueError("This link is not a direct image link and no preview image was found.")

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


def download_image(url: str) -> tuple[str, str, str]:
    """Download a direct image URL, or try a page's preview image as fallback."""
    response = get_response(url, stream=True)
    content_type = response.headers.get("Content-Type", "").lower()

    if content_type.startswith("image/"):
        path, image_type = save_image_response(response)
        return path, image_type, url

    # It is not a direct image. Try to read the page and find og:image/twitter:image.
    response.close()
    page_response = get_response(url, stream=False)
    try:
        page_type = page_response.headers.get("Content-Type", "").lower()
        if "html" not in page_type:
            raise ValueError("This link is not a direct image link.")

        html_text = page_response.text[:HTML_READ_LIMIT]
        image_url = extract_meta_image(page_response.url, html_text)
        if not image_url:
            raise ValueError("This link is not a direct image link and no preview image was found.")
    finally:
        page_response.close()

    image_response = get_response(image_url, stream=True)
    path, image_type = save_image_response(image_response)
    return path, image_type, image_url


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "👩‍💻 Hi, I am PhotoStiller.\n\n"
        "Send me a direct image URL or a page link that contains an image preview.\n\n"
        "Examples:\n"
        "https://example.com/image.jpg\n"
        "https://pin.it/example"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Send one or more image links.\n\n"
        f"Max image size: {MAX_IMAGE_MB} MB\n"
        f"Max links per message: {MAX_URLS_PER_MESSAGE}\n\n"
        "Works best with direct JPG, PNG, WEBP, GIF links. "
        "For normal web pages, I will try to extract the preview image."
    )


async def send_downloaded_file(message, chat_id: int, temp_path: str, content_type: str) -> None:
    with open(temp_path, "rb") as image_file:
        try:
            if "gif" in content_type or "svg" in content_type:
                await message.get_bot().send_document(
                    chat_id=chat_id,
                    document=image_file,
                    caption="Downloaded image ✅",
                    connect_timeout=30,
                    read_timeout=90,
                    write_timeout=90,
                    pool_timeout=30,
                )
            else:
                await message.get_bot().send_photo(
                    chat_id=chat_id,
                    photo=image_file,
                    caption="Downloaded image ✅",
                    connect_timeout=30,
                    read_timeout=90,
                    write_timeout=90,
                    pool_timeout=30,
                )
        except TimedOut:
            # Fallback: Telegram photo upload sometimes times out on free hosts.
            image_file.seek(0)
            await message.get_bot().send_document(
                chat_id=chat_id,
                document=image_file,
                caption="Downloaded image ✅",
                connect_timeout=30,
                read_timeout=120,
                write_timeout=120,
                pool_timeout=30,
            )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    urls = extract_urls_from_message(message)

    if not urls:
        await message.reply_text("Please send an image URL.")
        return

    for url in urls:
        temp_path = None
        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            temp_path, content_type, used_url = await asyncio.to_thread(download_image, url)
            await send_downloaded_file(message, update.effective_chat.id, temp_path, content_type)

            if used_url != url:
                await message.reply_text(f"Source image found from page:\n{used_url}")

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

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(90)
        .write_timeout(90)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler((filters.TEXT | filters.Caption()) & ~filters.COMMAND, handle_message))

    print("PhotoStiller bot is running with polling...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
