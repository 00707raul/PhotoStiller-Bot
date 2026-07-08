import asyncio
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    DOWNLOAD_ROOT,
    MAX_IMAGE_MB,
    MAX_RETRIES,
    MAX_STORAGE_GB,
    REQUEST_TIMEOUT,
)

# URL regex accepts Telegram usernames, t.me links, and most normal URLs.
URL_RE = re.compile(r"https?://[^\s<>'\"]+|t\.me/[^\s<>'\"]+|@[A-Za-z0-9_]{4,}", re.IGNORECASE)


def ensure_dirs() -> None:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)


def safe_name(value: str, fallback: str = "channel") -> str:
    value = (value or fallback).strip()
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    value = value.strip("._-")
    if not value:
        value = fallback
    return value[:80]


def clean_url(value: str) -> str:
    url = (value or "").strip()
    # Remove common punctuation pasted after links, but keep balanced parentheses used in real image URLs.
    while url and url[-1] in ".,;!?":
        url = url[:-1]
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    return url.strip()


def extract_urls(text: str) -> List[str]:
    raw = URL_RE.findall(text or "")
    return [clean_url(url) for url in raw if clean_url(url)]


def is_web_telegram_url(value: str) -> bool:
    try:
        parsed = urlparse(value if "://" in value else "https://" + value)
        return parsed.netloc.lower() == "web.telegram.org"
    except Exception:
        return False


def is_telegram_channel_link(value: str) -> bool:
    value = (value or "").strip()
    if value.startswith("@"):
        return True
    parsed = urlparse(value if "://" in value else "https://" + value)
    if parsed.netloc.lower() in {"t.me", "telegram.me"} and parsed.path.strip("/"):
        return True
    return False


def normalize_channel_input(value: str) -> str:
    value = clean_url(value)

    if value.startswith("@"):
        return value

    if value.startswith("t.me/") or value.startswith("telegram.me/"):
        return "https://" + value

    return value


def extract_invite_hash(value: str) -> Optional[str]:
    value = clean_url(value)
    if not value:
        return None

    normalized = value
    if normalized.startswith("t.me/") or normalized.startswith("telegram.me/"):
        normalized = "https://" + normalized

    try:
        parsed = urlparse(normalized)
    except Exception:
        return None

    if parsed.netloc.lower() not in {"t.me", "telegram.me"}:
        return None

    path = parsed.path.strip("/")
    if not path:
        return None

    if path.startswith("+") and len(path) > 1:
        return path[1:]

    parts = path.split("/")
    if len(parts) >= 2 and parts[0].lower() == "joinchat" and parts[1]:
        return parts[1]

    return None


def folder_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for file_path in path.rglob("*"):
        try:
            if file_path.is_file():
                total += file_path.stat().st_size
        except OSError:
            continue
    return total


def format_bytes(num: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def disk_report() -> str:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(DOWNLOAD_ROOT)
    downloads_size = folder_size_bytes(DOWNLOAD_ROOT)
    return (
        f"Free disk: {format_bytes(usage.free)}\n"
        f"Downloads folder: {format_bytes(downloads_size)}\n"
        f"Downloads limit: {MAX_STORAGE_GB:g} GB"
    )


def has_enough_disk_space() -> bool:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(DOWNLOAD_ROOT)
    free_gb = usage.free / (1024 ** 3)
    downloads_gb = folder_size_bytes(DOWNLOAD_ROOT) / (1024 ** 3)
    return free_gb >= 0.5 and downloads_gb < MAX_STORAGE_GB


def make_photo_path(folder: Path, message_id: int) -> Path:
    timestamp = int(time.time())
    return folder / f"{timestamp}_{message_id}.jpg"


def create_zip(folder: Path) -> Path:
    zip_path = folder.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, arcname=file_path.relative_to(folder))
    return zip_path


def cleanup_paths(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            if not path:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        except Exception:
            pass


def format_eta(done: int, started_at: float, remaining_guess: Optional[int] = None) -> str:
    elapsed = max(time.time() - started_at, 1)
    speed = done / elapsed if done else 0
    if not speed or remaining_guess is None:
        return "calculating..."
    seconds = int(remaining_guess / speed)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def is_valid_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _download_direct_image_sync(url: str, output_folder: Path) -> Path:
    output_folder.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                stream=True,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 PhotoSnatcherBot/2.0"},
            )
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()

            if not content_type.startswith("image/"):
                raise ValueError("This link is not a direct image link.")

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_MB * 1024 * 1024:
                raise ValueError(f"Image is too large. Max size is {MAX_IMAGE_MB} MB.")

            extension = content_type.split("/")[-1] or "jpg"
            if extension == "jpeg":
                extension = "jpg"
            if extension not in {"jpg", "png", "webp", "gif", "bmp"}:
                extension = "jpg"

            path = output_folder / f"direct_{int(time.time() * 1000)}.{extension}"
            max_bytes = MAX_IMAGE_MB * 1024 * 1024
            total = 0
            with open(path, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        path.unlink(missing_ok=True)
                        raise ValueError(f"Image is too large. Max size is {MAX_IMAGE_MB} MB.")
                    file.write(chunk)
            return path
        except Exception as exc:
            last_error = exc
            time.sleep(2 ** (attempt - 1))

    raise RuntimeError(str(last_error) if last_error else "Download failed.")


def _extract_og_image_sync(url: str) -> Optional[str]:
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 PhotoSnatcherBot/2.0"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for selector in [
            ("meta", {"property": "og:image"}),
            ("meta", {"name": "twitter:image"}),
            ("meta", {"property": "og:image:secure_url"}),
        ]:
            tag = soup.find(*selector)
            if tag and tag.get("content"):
                return urljoin(url, tag["content"])
    except Exception:
        return None
    return None


async def download_direct_image(url: str, output_folder: Path) -> Path:
    return await asyncio.to_thread(_download_direct_image_sync, url, output_folder)


async def extract_og_image(url: str) -> Optional[str]:
    return await asyncio.to_thread(_extract_og_image_sync, url)
