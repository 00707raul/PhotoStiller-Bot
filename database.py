import sqlite3
import threading
from pathlib import Path
from typing import Optional

from config import DATABASE_PATH


class DownloadDB:
    def __init__(self, db_path: Path = DATABASE_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloaded_images (
                    channel_key TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(channel_key, message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    user_id INTEGER PRIMARY KEY,
                    channel_key TEXT,
                    status TEXT,
                    downloaded_count INTEGER DEFAULT 0,
                    total_seen INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get_file_path(self, channel_key: str, message_id: int) -> Optional[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT file_path FROM downloaded_images WHERE channel_key = ? AND message_id = ?",
                (channel_key, message_id),
            ).fetchone()
            return row[0] if row else None

    def is_downloaded_and_exists(self, channel_key: str, message_id: int) -> bool:
        file_path = self.get_file_path(channel_key, message_id)
        return bool(file_path and Path(file_path).exists())

    def mark_downloaded(self, channel_key: str, message_id: int, file_path: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO downloaded_images(channel_key, message_id, file_path)
                VALUES (?, ?, ?)
                """,
                (channel_key, message_id, file_path),
            )

    def remove_channel_records(self, channel_key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM downloaded_images WHERE channel_key = ?", (channel_key,))

    def set_job(self, user_id: int, channel_key: str, status: str, downloaded_count: int, total_seen: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(user_id, channel_key, status, downloaded_count, total_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    channel_key = excluded.channel_key,
                    status = excluded.status,
                    downloaded_count = excluded.downloaded_count,
                    total_seen = excluded.total_seen,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, channel_key, status, downloaded_count, total_seen),
            )

    def get_job(self, user_id: int):
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT channel_key, status, downloaded_count, total_seen, updated_at FROM jobs WHERE user_id = ?",
                (user_id,),
            ).fetchone()

    def clear_job(self, user_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM jobs WHERE user_id = ?", (user_id,))
