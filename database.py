import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import DATABASE_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    is_banned INTEGER DEFAULT 0,
                    first_seen TEXT,
                    last_seen TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_daily (
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    direct_count INTEGER DEFAULT 0,
                    channel_count INTEGER DEFAULT 0,
                    images_downloaded INTEGER DEFAULT 0,
                    PRIMARY KEY(user_id, day)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_type TEXT,
                    detail TEXT,
                    created_at TEXT
                )
                """
            )

    # Download tracking
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

    # Users / moderation
    def touch_user(self, user_id: int, username: str = "", first_name: str = "", last_name: str = "") -> None:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, username, first_name, last_name, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_seen = excluded.last_seen
                """,
                (user_id, username or "", first_name or "", last_name or "", now, now),
            )

    def is_banned(self, user_id: int) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return bool(row and row[0])

    def set_banned(self, user_id: int, banned: bool) -> None:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, is_banned, first_seen, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET is_banned = excluded.is_banned, last_seen = excluded.last_seen
                """,
                (user_id, 1 if banned else 0, now, now),
            )

    def list_users(self, limit: int = 20):
        with self._lock, self._connect() as conn:
            return conn.execute(
                """
                SELECT user_id, username, first_name, is_banned, first_seen, last_seen
                FROM users ORDER BY last_seen DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def list_banned(self, limit: int = 50):
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT user_id, username, first_name, last_seen FROM users WHERE is_banned = 1 ORDER BY last_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def user_count(self) -> int:
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # Usage / quotas
    def add_usage(self, user_id: int, direct: int = 0, channel: int = 0, images: int = 0) -> None:
        day = _today()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_daily(user_id, day, direct_count, channel_count, images_downloaded)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, day) DO UPDATE SET
                    direct_count = direct_count + excluded.direct_count,
                    channel_count = channel_count + excluded.channel_count,
                    images_downloaded = images_downloaded + excluded.images_downloaded
                """,
                (user_id, day, direct, channel, images),
            )

    def get_usage_today(self, user_id: int):
        day = _today()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT direct_count, channel_count, images_downloaded FROM usage_daily WHERE user_id = ? AND day = ?",
                (user_id, day),
            ).fetchone()
            return row or (0, 0, 0)


    def usage_totals_all_time(self):
        with self._lock, self._connect() as conn:
            return conn.execute(
                """
                SELECT
                    COALESCE(SUM(direct_count),0),
                    COALESCE(SUM(channel_count),0),
                    COALESCE(SUM(images_downloaded),0)
                FROM usage_daily
                """
            ).fetchone()

    def active_users_since(self, days: int = 7) -> int:
        with self._lock, self._connect() as conn:
            return conn.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE datetime(last_seen) >= datetime('now', ?)
                """,
                (f"-{int(days)} days",),
            ).fetchone()[0]

    def daily_usage_last_days(self, days: int = 14):
        with self._lock, self._connect() as conn:
            return conn.execute(
                """
                SELECT day,
                       COALESCE(SUM(direct_count),0) AS direct_total,
                       COALESCE(SUM(channel_count),0) AS channel_total,
                       COALESCE(SUM(images_downloaded),0) AS image_total
                FROM usage_daily
                WHERE date(day) >= date('now', ?)
                GROUP BY day
                ORDER BY day DESC
                LIMIT ?
                """,
                (f"-{int(days)} days", int(days)),
            ).fetchall()

    def last_user_seen_iso(self) -> str:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT MAX(last_seen) FROM users").fetchone()
            return row[0] or "never"

    def usage_totals_today(self):
        day = _today()
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT COALESCE(SUM(direct_count),0), COALESCE(SUM(channel_count),0), COALESCE(SUM(images_downloaded),0) FROM usage_daily WHERE day = ?",
                (day,),
            ).fetchone()

    # Runtime settings
    def get_setting(self, key: str) -> Optional[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, _now_iso()),
            )

    def log_event(self, user_id: int, event_type: str, detail: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events(user_id, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
                (user_id, event_type, detail[:1000], _now_iso()),
            )

    def recent_events(self, limit: int = 20):
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT user_id, event_type, detail, created_at FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
