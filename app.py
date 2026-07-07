import ipaddress
import os
import re
import socket
import tempfile
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "10"))
MAX_IMAGE_SIZE = MAX_IMAGE_MB * 1024 * 1024
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def send_message(chat_id: int | str, text: str) -> None:
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing")
        return

    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=REQUEST_TIMEOUT,
    )


def send_photo(chat_id: int | str, file_path: str, content_type: str, caption: str = "") -> None:
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing")
        return

    filename = os.path.basename(file_path)
    with open(file_path, "rb") as image_file:
        requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": (filename, image_file, content_type)},
            timeout=REQUEST_TIMEOUT,
        )


def is_public_hostname(hostname: str) -> bool:
    """Block localhost/private/internal IPs so the bot cannot fetch internal services."""
    if not hostname:
        return False

    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    if hostname.lower() in blocked_hosts:
        return False

    try:
        addresses = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False

    return True


def validate_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return False, "Only http and https links are supported."

    if not parsed.netloc or not parsed.hostname:
        return False, "This URL is not valid."

    if not is_public_hostname(parsed.hostname):
        return False, "This URL is not allowed. Send a public image URL."

    return True, ""


def download_image(url: str) -> tuple[bool, str, str, str]:
    """
    Returns: success, file_path_or_empty, content_type_or_empty, error_message_or_empty
    """
    ok, error = validate_url(url)
    if not ok:
        return False, "", "", error

    try:
        response = requests.get(
            url,
            stream=True,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "PhotoStillerBot/1.0 (+Telegram bot)"},
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").split(";")[0].lower().strip()
        if content_type not in ALLOWED_IMAGE_TYPES:
            return False, "", "", "This link is not a direct JPG, PNG, WEBP, or GIF image."

        content_length = int(response.headers.get("Content-Length", "0") or 0)
        if content_length > MAX_IMAGE_SIZE:
            return False, "", "", f"Image is too large. Max size is {MAX_IMAGE_MB} MB."

        suffix = ALLOWED_IMAGE_TYPES[content_type]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            total_size = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue

                total_size += len(chunk)
                if total_size > MAX_IMAGE_SIZE:
                    tmp_file.close()
                    os.remove(tmp_file.name)
                    return False, "", "", f"Image is too large. Max size is {MAX_IMAGE_MB} MB."

                tmp_file.write(chunk)

            return True, tmp_file.name, content_type, ""

    except requests.exceptions.Timeout:
        return False, "", "", "Download timed out. Try another image link."
    except requests.exceptions.RequestException:
        return False, "", "", "Failed to download the image. Make sure the link is public."
    except Exception as exc:
        return False, "", "", f"Unexpected error: {exc}"


def handle_message(message: dict) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id:
        return

    if text.startswith("/start"):
        send_message(
            chat_id,
            "👩‍💻 Welcome to PhotoStiller!\n\nSend me a direct image URL and I will download it for you.\n\nSupported: JPG, PNG, WEBP, GIF.",
        )
        return

    if text.startswith("/help"):
        send_message(
            chat_id,
            "Send a direct image link, for example:\nhttps://example.com/photo.jpg\n\nI will download it and send it back as an image.",
        )
        return

    urls = URL_RE.findall(text)
    if not urls:
        send_message(chat_id, "Send me a direct image URL, for example: https://site.com/image.jpg")
        return

    url = urls[0].rstrip(".,)]}")
    send_message(chat_id, "Downloading image...")

    success, file_path, content_type, error = download_image(url)
    if not success:
        send_message(chat_id, f"❌ {error}")
        return

    try:
        send_photo(chat_id, file_path, content_type, "✅ Image downloaded by PhotoStiller")
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass


@app.get("/")
def home():
    return jsonify(
        {
            "status": "ok",
            "bot": "PhotoStiller",
            "message": "Bot server is running. Use /healthz for health check.",
        }
    )


@app.get("/healthz")
def healthz():
    return jsonify({"status": "healthy"})


@app.post("/webhook/<secret>")
def telegram_webhook(secret: str):
    if not WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "WEBHOOK_SECRET is not configured"}), 500

    if secret != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message")

    if message:
        handle_message(message)

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
