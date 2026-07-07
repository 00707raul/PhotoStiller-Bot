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
    UserAlreadyParticipantError,
)
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import Chat, Channel, InputMessagesFilterPhotos

from config import (
    ALBUM_BATCH_SIZE,
    DOWNLOAD_ROOT,
    DOWNLOAD_TIMEOUT,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_ACTIVE_JOBS,
    MAX_RETRIES,
    PROGRESS_UPDATE_INTERVAL,
    PROGRESS_EDIT_INTERVAL_SECONDS,
    TELEGRAM_ZIP_LIMIT_BYTES,
)
from database import DownloadDB
from utils import (
    cleanup_paths,
    create_zip,
    extract_invite_hash,
    format_eta,
    has_enough_disk_space,
    make_photo_path,
    normalize_channel_input,
    safe_name,
)


BAR_LENGTH = 18


@dataclass
class DownloadJob:
    user_id: int
    channel_input: str
    channel_key: str = ""
    status: str = "validating"
    phase: str = "Starting"
    downloaded_count: int = 0
    total_seen: int = 0
    total_photos: int = 0
    processed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    started_at: float = field(default_factory=time.time)
    folder: Optional[Path] = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    last_message_id: Optional[int] = None
    progress_message = None
    progress_message_ids: list[int] = field(default_factory=list)

    def __post_init__(self):
        self.pause_event.set()


class ChannelImageDownloader:
    """Download Telegram channel photos with an editable live progress bar."""

    def __init__(self, bot_client, reader_client, db: DownloadDB, logger, uses_user_session: bool = False):
        self.bot_client = bot_client
        self.reader_client = reader_client
        self.db = db
        self.logger = logger
        self.uses_user_session = uses_user_session
        self.jobs: Dict[int, DownloadJob] = {}

    def get_job(self, user_id: int) -> Optional[DownloadJob]:
        return self.jobs.get(user_id)

    async def _resolve_invite_link(self, invite_hash: str):
        if not self.uses_user_session:
            raise PermissionError(
                "Private invite links require STRING_SESSION. Add your Telegram user session to Render env first."
            )

        try:
            result = await self.reader_client(ImportChatInviteRequest(invite_hash))
            if getattr(result, "chats", None):
                return result.chats[0]
        except UserAlreadyParticipantError:
            try:
                invite = await self.reader_client(CheckChatInviteRequest(invite_hash))
                chat = getattr(invite, "chat", None)
                if chat:
                    return chat
            except Exception:
                pass
            return await self.reader_client.get_entity(f"https://t.me/+{invite_hash}")
        except (InviteHashInvalidError, InviteHashExpiredError, InviteHashEmptyError):
            raise PermissionError("This private invite link is invalid or expired.")
        except RPCError as exc:
            raise PermissionError(f"Cannot join/open this private invite link: {exc}")

        raise PermissionError("Could not open this private invite link. It may require join approval or be expired.")

    async def validate_channel(self, channel_input: str):
        normalized = normalize_channel_input(channel_input)
        invite_hash = extract_invite_hash(normalized)

        try:
            if invite_hash:
                entity = await self._resolve_invite_link(invite_hash)
            else:
                entity = await self.reader_client.get_entity(normalized)
        except PermissionError:
            raise
        except (ChannelPrivateError, InviteHashInvalidError, InviteHashExpiredError, InviteHashEmptyError):
            if self.uses_user_session:
                raise PermissionError("Channel is private or inaccessible for your user session.")
            raise PermissionError("Channel is private or inaccessible. Add STRING_SESSION or add the bot to the channel.")
        except (ChannelInvalidError, ValueError, RPCError) as exc:
            raise ValueError(f"Invalid or inaccessible channel link: {exc}")

        if not isinstance(entity, (Channel, Chat)):
            raise ValueError("This link does not look like a Telegram channel/group.")

        return entity

    async def start_download(self, user_id: int, channel_input: str) -> str:
        if user_id in self.jobs and self.jobs[user_id].status in {"validating", "scanning", "running", "paused", "zipping", "delivering"}:
            return "A download is already running. Use /status or /cancel first."

        active_jobs = [job for job in self.jobs.values() if job.status in {"validating", "scanning", "running", "paused", "zipping", "delivering"}]
        if len(active_jobs) >= MAX_ACTIVE_JOBS:
            return f"Server is busy. Maximum active downloads reached ({MAX_ACTIVE_JOBS}). Try again later."

        if not has_enough_disk_space():
            return "Not enough disk space or storage limit reached. Use /cleanup first."

        job = DownloadJob(user_id=user_id, channel_input=channel_input)
        self.jobs[user_id] = job
        asyncio.create_task(self._run_download(job))
        return "Download started. I will show a live progress bar here. Use /status or /cancel."

    async def cancel(self, user_id: int) -> str:
        job = self.jobs.get(user_id)
        if not job:
            return "No active download to cancel."
        job.status = "cancelled"
        job.phase = "Cancelling"
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
            job.phase = "Downloading"
            job.pause_event.set()
            return "Download resumed."
        if job.status == "running":
            job.status = "paused"
            job.phase = "Paused"
            job.pause_event.clear()
            return "Download paused. Use /pause again to resume."
        return f"Cannot pause/resume while status is {job.status}."

    def _percent(self, job: DownloadJob) -> float:
        if job.total_photos <= 0:
            return 0.0
        return min(100.0, (job.processed_count / job.total_photos) * 100.0)

    def _bar(self, percent: float) -> str:
        filled = int((percent / 100.0) * BAR_LENGTH)
        filled = max(0, min(BAR_LENGTH, filled))
        return "█" * filled + "░" * (BAR_LENGTH - filled)

    def _progress_text(self, job: DownloadJob) -> str:
        elapsed = max(time.time() - job.started_at, 1)
        speed = job.downloaded_count / elapsed if job.downloaded_count else 0
        percent = self._percent(job)

        if job.total_photos > 0:
            remaining = max(job.total_photos - job.processed_count, 0)
            eta = format_eta(job.processed_count, job.started_at, remaining)
            progress_line = f"{job.processed_count}/{job.total_photos} photos processed"
        else:
            eta = "calculating..."
            progress_line = f"{job.total_seen} messages scanned"

        return (
            f"📥 **PhotoStiller Download**\n"
            f"Channel: `{job.channel_key or job.channel_input}`\n"
            f"Status: **{job.status}**\n"
            f"Phase: **{job.phase}**\n\n"
            f"`{self._bar(percent)}` **{percent:.1f}%**\n"
            f"{progress_line}\n\n"
            f"✅ Downloaded: {job.downloaded_count}\n"
            f"↩️ Skipped/resumed: {job.skipped_count}\n"
            f"⚠️ Failed: {job.failed_count}\n"
            f"🔎 Messages scanned: {job.total_seen}\n"
            f"⚡ Speed: {speed:.2f} images/sec\n"
            f"⏳ ETA: {eta}"
        )

    def status_text(self, user_id: int) -> str:
        job = self.jobs.get(user_id)
        if not job:
            saved = self.db.get_job(user_id)
            if saved:
                channel_key, status, downloaded_count, total_seen, updated_at = saved
                return (
                    "No active download. Last saved job:\n"
                    f"Channel: {channel_key}\n"
                    f"Status: {status}\n"
                    f"Images downloaded: {downloaded_count}\n"
                    f"Messages scanned: {total_seen}\n"
                    f"Updated: {updated_at}"
                )
            return "No active download."
        return self._progress_text(job)

    async def _create_progress_message(self, user_id: int, job: DownloadJob) -> None:
        msg = await self.bot_client.send_message(
            user_id,
            self._progress_text(job),
            buttons=[[Button.inline("Cancel", data=b"cancel"), Button.inline("Pause/Resume", data=b"pause")]],
            parse_mode="md",
        )
        job.progress_message = msg
        try:
            job.progress_message_ids.append(msg.id)
        except Exception:
            pass

    async def _edit_progress_loop(self, user_id: int, job: DownloadJob) -> None:
        last_text = ""
        while job.status not in {"finished", "cancelled", "failed"}:
            if job.progress_message:
                try:
                    text = self._progress_text(job)
                    if text != last_text:
                        await job.progress_message.edit(
                            text,
                            buttons=[[Button.inline("Cancel", data=b"cancel"), Button.inline("Pause/Resume", data=b"pause")]],
                            parse_mode="md",
                        )
                        last_text = text
                except Exception as exc:
                    self.logger.warning("Progress message edit failed: %s", exc)
            await asyncio.sleep(PROGRESS_EDIT_INTERVAL_SECONDS)

    async def _delete_progress_messages(self, job: DownloadJob) -> None:
        if not job.progress_message_ids:
            return
        for message_id in list(set(job.progress_message_ids)):
            try:
                await self.bot_client.delete_messages(job.user_id, message_id)
            except Exception as exc:
                self.logger.warning("Could not delete progress message %s: %s", message_id, exc)

    async def _count_photos(self, entity, job: DownloadJob) -> int:
        job.status = "scanning"
        job.phase = "Scanning channel to calculate total photos"
        count = 0
        scanned = 0
        async for message in self.reader_client.iter_messages(entity, filter=InputMessagesFilterPhotos):
            if job.cancel_event.is_set():
                raise asyncio.CancelledError()
            await job.pause_event.wait()
            scanned += 1
            count += 1
            job.total_seen = scanned
            job.total_photos = count
            if scanned % 250 == 0:
                self.db.set_job(job.user_id, job.channel_key, job.status, job.downloaded_count, job.total_seen)
                await asyncio.sleep(0)
        return count

    async def _run_download(self, job: DownloadJob) -> None:
        user_id = job.user_id
        zip_path: Optional[Path] = None
        progress_task: Optional[asyncio.Task] = None

        try:
            entity = await self.validate_channel(job.channel_input)

            channel_key = safe_name(getattr(entity, "username", None) or getattr(entity, "title", None) or str(entity.id))
            job.channel_key = channel_key
            job.folder = DOWNLOAD_ROOT / channel_key
            job.folder.mkdir(parents=True, exist_ok=True)
            self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)

            await self._create_progress_message(user_id, job)
            progress_task = asyncio.create_task(self._edit_progress_loop(user_id, job))

            reader_mode = "user session" if self.uses_user_session else "bot session"
            self.logger.info("Validated channel %s with %s", channel_key, reader_mode)

            # First pass: count photos so percentage is accurate.
            total_photos = await self._count_photos(entity, job)
            job.total_seen = 0
            job.total_photos = total_photos
            job.status = "running"
            job.phase = "Downloading photos"
            self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)

            if total_photos == 0:
                job.status = "finished"
                job.phase = "Finished"
                await self.bot_client.send_message(user_id, "No images found in this channel.")
                return

            semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
            pending = []
            last_progress = 0

            # Second pass: download photos.
            async for message in self.reader_client.iter_messages(entity, filter=InputMessagesFilterPhotos):
                if job.cancel_event.is_set():
                    raise asyncio.CancelledError()

                await job.pause_event.wait()
                job.total_seen += 1
                job.last_message_id = message.id

                if not message.photo:
                    continue

                if self.db.is_downloaded_and_exists(channel_key, message.id):
                    job.skipped_count += 1
                    job.processed_count += 1
                    continue

                pending.append(asyncio.create_task(self._download_one(job, channel_key, message, semaphore)))

                if len(pending) >= MAX_CONCURRENT_DOWNLOADS:
                    results = await asyncio.gather(*pending, return_exceptions=True)
                    pending.clear()
                    for result in results:
                        job.processed_count += 1
                        if result is True:
                            job.downloaded_count += 1
                        else:
                            job.failed_count += 1

                    if job.downloaded_count - last_progress >= PROGRESS_UPDATE_INTERVAL:
                        last_progress = job.downloaded_count
                        self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)

            if pending:
                results = await asyncio.gather(*pending, return_exceptions=True)
                for result in results:
                    job.processed_count += 1
                    if result is True:
                        job.downloaded_count += 1
                    else:
                        job.failed_count += 1
                pending.clear()

            if job.cancel_event.is_set():
                raise asyncio.CancelledError()

            if job.downloaded_count == 0:
                job.status = "finished"
                job.phase = "Finished"
                await self.bot_client.send_message(user_id, "No new images found in this channel.")
                return

            job.status = "zipping"
            job.phase = "Creating ZIP archive"
            self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)

            zip_path = create_zip(job.folder)
            zip_size = zip_path.stat().st_size if zip_path.exists() else 0

            job.status = "delivering"
            job.phase = "Sending ZIP to Telegram"
            self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)

            if zip_size <= TELEGRAM_ZIP_LIMIT_BYTES:
                await self.bot_client.send_file(
                    user_id,
                    str(zip_path),
                    caption=f"✅ Done. Downloaded {job.downloaded_count} images from {channel_key}.",
                    force_document=True,
                )
            else:
                job.phase = "ZIP too large, sending albums"
                await self._send_album_batches(user_id, job.folder)
                await self.bot_client.send_message(
                    user_id,
                    f"✅ Done. ZIP was too large, so images were sent in batches. Total downloaded: {job.downloaded_count}.",
                )

            job.status = "finished"
            job.phase = "Finished"
            self.db.set_job(user_id, channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.bot_client.send_message(user_id, "✅ Cleanup complete. Temporary progress message and server files were removed.")

        except asyncio.CancelledError:
            job.status = "cancelled"
            job.phase = "Cancelled"
            self.db.set_job(user_id, job.channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.bot_client.send_message(user_id, f"Cancelled. Downloaded before cancel: {job.downloaded_count} images.")
        except PermissionError as exc:
            job.status = "failed"
            job.phase = "Failed"
            self.db.set_job(user_id, job.channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.bot_client.send_message(user_id, f"❌ {exc}")
        except Exception as exc:
            self.logger.exception("Download failed")
            job.status = "failed"
            job.phase = "Failed"
            self.db.set_job(user_id, job.channel_key, job.status, job.downloaded_count, job.total_seen)
            await self.bot_client.send_message(user_id, f"❌ Download failed: {exc}")
        finally:
            if progress_task:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

            if job.status in {"finished", "cancelled", "failed"}:
                await self._delete_progress_messages(job)
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

    async def _send_album_batches(self, user_id: int, folder: Path) -> None:
        images = [str(path) for path in sorted(folder.glob("*.jpg"))]
        for i in range(0, len(images), ALBUM_BATCH_SIZE):
            batch = images[i : i + ALBUM_BATCH_SIZE]
            try:
                await self.bot_client.send_file(user_id, batch, album=True)
            except Exception:
                for image in batch:
                    await self.bot_client.send_file(user_id, image)
            await asyncio.sleep(1)
