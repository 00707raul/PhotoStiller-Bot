import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from telethon import Button
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    FloodWaitError,
    InviteHashEmptyError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    RPCError,
)
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import Channel, ChannelParticipantAdmin, ChannelParticipantCreator, InputMessagesFilterPhotos

from config import (
    ALBUM_BATCH_SIZE,
    DOWNLOAD_ROOT,
    DOWNLOAD_TIMEOUT,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_RETRIES,
    PROGRESS_UPDATE_INTERVAL,
    TELEGRAM_ZIP_LIMIT_BYTES,
)
from database import DownloadDB
from utils import (
    cleanup_paths,
    create_zip,
    format_eta,
    has_enough_disk_space,
    make_photo_path,
    normalize_channel_input,
    safe_name,
)


@dataclass
class DownloadJob:
    user_id: int
    channel_input: str
    channel_key: str = ""
    status: str = "validating"
    downloaded_count: int = 0
    total_seen: int = 0
    skipped_count: int = 0
    started_at: float = field(default_factory=time.time)
    folder: Optional[Path] = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    last_message_id: Optional[int] = None

    def __post_init__(self):
        self.pause_event.set()


class ChannelImageDownloader:
    def __init__(self, client, db: DownloadDB, logger):
        self.client = client
        self.db = db
        self.logger = logger
        self.jobs: Dict[int, DownloadJob] = {}

    def get_job(self, user_id: int) -> Optional[DownloadJob]:
        return self.jobs.get(user_id)

    async def validate_channel(self, channel_input: str):
        normalized = normalize_channel_input(channel_input)
        try:
            entity = await self.client.get_entity(normalized)
        except (ChannelPrivateError, InviteHashInvalidError, InviteHashExpiredError, InviteHashEmptyError):
            raise PermissionError("Channel is private or inaccessible. Add the bot as an admin first.")
        except (ChannelInvalidError, ValueError, RPCError) as exc:
            raise ValueError(f"Invalid or inaccessible channel link: {exc}")

        if not isinstance(entity, Channel):
            raise ValueError("This link does not look like a Telegram channel.")

        # The user explicitly wants admin-only channel history reading.
        # Public channels may be readable without admin, but we enforce admin access for safety and private-channel reliability.
        me = await self.client.get_me()
        try:
            participant = await self.client(GetParticipantRequest(channel=entity, participant=me.id))
            participant_type = type(participant.participant)
            if participant_type not in (ChannelParticipantAdmin, ChannelParticipantCreator):
                raise PermissionError("This bot must be added as an admin to this channel to read history.")
        except PermissionError:
            raise
        except Exception:
            raise PermissionError("This bot must be added as an admin to this channel to read history.")

        return entity

    async def start_download(self, user_id: int, channel_input: str) -> str:
        if user_id in self.jobs and self.jobs[user_id].status in {"validating", "running", "paused", "delivering"}:
            return "A download is already running. Use /status or /cancel first."

        if not has_enough_disk_space():
            return "Not enough disk space or storage limit reached. Clean the server storage first."

        job = DownloadJob(user_id=user_id, channel_input=channel_input)
        self.jobs[user_id] = job
        asyncio.create_task(self._run_download(job))
        return "Download started. Use /status to see progress or /cancel to stop."

    async def cancel(self, user_id: int) -> str:
        job = self.jobs.get(user_id)
        if not job:
            return "No active download to cancel."
        job.status = "cancelled"
        job.cancel_event.set()
        job.pause_event.set()
        self.db.set_job(user_id, job.channel_key, job.status, job.downloaded_count, job.total_seen)
        return "Download cancellation requested."

    async def toggle_pause(self, user_id: int) -> str:
        job = self.jobs.get(user_id)
        if not job:
            return "No active download."
        if job.status == "paused":
            job.status = "running"
            job.pause_event.set()
            return "Download resumed."
        if job.status == "running":
            job.status = "paused"
            job.pause_event.clear()
            return "Download paused. Press Resume or use /pause again."
        return f"Cannot pause/resume while status is {job.status}."

    def status_text(self, user_id: int) -> str:
        job = self.jobs.get(user_id)
        if not job:
            db_job = self.db.get_job(user_id)
            if db_job:
                channel_key, status, downloaded_count, total_seen, updated_at = db_job
                return (
                    f"Last job: {status}\n"
                    f"Channel: {channel_key or 'unknown'}\n"
                    f"Downloaded: {downloaded_count}\n"
                    f"Messages seen: {total_seen}\n"
                    f"Updated: {updated_at}"
                )
            return "No active download."

        elapsed = max(time.time() - job.started_at, 1)
        speed = job.downloaded_count / elapsed if job.downloaded_count else 0
        return (
            f"Status: {job.status}\n"
            f"Channel: {job.channel_key or job.channel_input}\n"
            f"Images downloaded: {job.downloaded_count}\n"
            f"Images skipped/resumed: {job.skipped_count}\n"
            f"Messages scanned: {job.total_seen}\n"
            f"Speed: {speed:.2f} images/sec"
        )

    async def _run_download(self, job: DownloadJob) -> None:
        user_id = job.user_id
        zip_path: Optional[Path] = None

        try:
            await self.client.send_message(user_id, "Validating channel access...")
            entity = await self.validate_channel(job.channel_input)

            channel_key = safe_name(getattr(entity, "username", None) or getattr(entity, "title", None) or str(entity.id))
            job.channel_key = channel_key
            job.folder = DOWNLOAD_ROOT / channel_key
            job.folder.mkdir(parents=True, exist_ok=True)
            job.status = "running"
            self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)

            await self.client.send_message(
                user_id,
                f"✅ Channel validated: {getattr(entity, 'title', channel_key)}\nStarting image scan...",
                buttons=[[Button.inline("Cancel", data=b"cancel"), Button.inline("Pause", data=b"pause")]],
            )

            semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
            pending = []
            last_progress = 0

            async for message in self.client.iter_messages(entity, filter=InputMessagesFilterPhotos):
                if job.cancel_event.is_set():
                    raise asyncio.CancelledError()

                await job.pause_event.wait()
                job.total_seen += 1
                job.last_message_id = message.id

                if not message.photo:
                    continue

                if self.db.is_downloaded_and_exists(channel_key, message.id):
                    job.skipped_count += 1
                    continue

                pending.append(asyncio.create_task(self._download_one(job, channel_key, message, semaphore)))

                if len(pending) >= MAX_CONCURRENT_DOWNLOADS:
                    results = await asyncio.gather(*pending, return_exceptions=True)
                    pending.clear()
                    for result in results:
                        if result is True:
                            job.downloaded_count += 1

                    await self._maybe_progress(user_id, job, last_progress)
                    if job.downloaded_count - last_progress >= PROGRESS_UPDATE_INTERVAL:
                        last_progress = job.downloaded_count
                    self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)

            if pending:
                results = await asyncio.gather(*pending, return_exceptions=True)
                for result in results:
                    if result is True:
                        job.downloaded_count += 1
                pending.clear()

            if job.cancel_event.is_set():
                raise asyncio.CancelledError()

            if job.downloaded_count == 0:
                job.status = "finished"
                await self.client.send_message(user_id, "No new images found in this channel.")
                return

            job.status = "delivering"
            self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.client.send_message(user_id, f"Creating ZIP archive with {job.downloaded_count} images...")

            zip_path = create_zip(job.folder)
            zip_size = zip_path.stat().st_size if zip_path.exists() else 0

            if zip_size <= TELEGRAM_ZIP_LIMIT_BYTES:
                await self.client.send_file(
                    user_id,
                    str(zip_path),
                    caption=f"✅ Done. Downloaded {job.downloaded_count} images from {channel_key}.",
                    force_document=True,
                )
            else:
                await self.client.send_message(
                    user_id,
                    "ZIP is too large, sending images in batches of 10 instead...",
                )
                await self._send_album_batches(user_id, job.folder)

            job.status = "finished"
            self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.client.send_message(user_id, "Cleanup complete. Server storage has been cleared.")

        except asyncio.CancelledError:
            job.status = "cancelled"
            self.db.set_job(user_id, job.channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.client.send_message(user_id, f"Cancelled. Downloaded before cancel: {job.downloaded_count} images.")
        except PermissionError as exc:
            job.status = "failed"
            self.db.set_job(user_id, job.channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.client.send_message(user_id, f"❌ {exc}")
        except Exception as exc:
            self.logger.exception("Download failed")
            job.status = "failed"
            self.db.set_job(user_id, job.channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.client.send_message(user_id, f"❌ Download failed: {exc}")
        finally:
            if job.status in {"finished", "cancelled", "failed"}:
                cleanup = []
                if zip_path:
                    cleanup.append(zip_path)
                if job.folder and job.status == "finished":
                    cleanup.append(job.folder)
                    if job.channel_key:
                        self.db.remove_channel_records(job.channel_key)
                cleanup_paths(cleanup)
                self.jobs.pop(user_id, None)

    async def _download_one(self, job: DownloadJob, channel_key: str, message, semaphore: asyncio.Semaphore) -> bool:
        async with semaphore:
            await job.pause_event.wait()
            if job.cancel_event.is_set():
                return False

            assert job.folder is not None
            file_path = make_photo_path(job.folder, message.id)

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    await asyncio.sleep(0.5)
                    downloaded_path = await asyncio.wait_for(
                        message.download_media(file=str(file_path)),
                        timeout=DOWNLOAD_TIMEOUT,
                    )
                    if downloaded_path:
                        self.db.mark_downloaded(channel_key, message.id, str(downloaded_path))
                        return True
                    return False
                except FloodWaitError as exc:
                    wait_seconds = int(getattr(exc, "seconds", 30)) + (attempt * 2)
                    self.logger.warning("Flood wait hit. Sleeping for %s seconds", wait_seconds)
                    await asyncio.sleep(wait_seconds)
                except Exception as exc:
                    self.logger.warning("Retry %s/%s for message %s failed: %s", attempt, MAX_RETRIES, message.id, exc)
                    await asyncio.sleep(2 ** (attempt - 1))

            self.logger.error("Failed to download message %s after retries", message.id)
            return False

    async def _maybe_progress(self, user_id: int, job: DownloadJob, last_progress: int) -> None:
        if job.downloaded_count and job.downloaded_count - last_progress >= PROGRESS_UPDATE_INTERVAL:
            eta = format_eta(job.downloaded_count, job.started_at, None)
            await self.client.send_message(
                user_id,
                f"Downloaded {job.downloaded_count} images so far...\nETA: {eta}",
                buttons=[[Button.inline("Cancel", data=b"cancel"), Button.inline("Pause/Resume", data=b"pause")]],
            )

    async def _send_album_batches(self, user_id: int, folder: Path) -> None:
        images = [str(path) for path in sorted(folder.glob("*.jpg"))]
        for i in range(0, len(images), ALBUM_BATCH_SIZE):
            batch = images[i : i + ALBUM_BATCH_SIZE]
            try:
                await self.client.send_file(user_id, batch, album=True)
            except Exception:
                # Fallback one-by-one if album upload fails.
                for image in batch:
                    await self.client.send_file(user_id, image)
            await asyncio.sleep(1)
