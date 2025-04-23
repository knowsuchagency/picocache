from __future__ import annotations
from typing import Any

import pickle
import sqlite3
import threading
import time

from .base import _BaseCache, _MISSING


class SQLiteCache(_BaseCache):
    """Persistent cache backed by SQLite."""

    _TABLE_NAME = "picocache"

    def __init__(
        self,
        db_path: str = "picocache.db",
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        with conn:
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._TABLE_NAME} (
                    key TEXT PRIMARY KEY,
                    value BLOB,
                    last_accessed REAL
                )"""
            )
        return conn

    def _lookup(self, key: str) -> Any | _MISSING:
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute(
                    f"SELECT value FROM {self._TABLE_NAME} WHERE key = ?", (key,)
                )
                row = cursor.fetchone()
                if row is None:
                    return _MISSING

                # Update last_accessed time on lookup
                cursor.execute(
                    f"UPDATE {self._TABLE_NAME} SET last_accessed = ? WHERE key = ?",
                    (time.time(), key),
                )
                self._conn.commit()
                self._hits += 1  # Increment hit counter
                return pickle.loads(row[0])
            finally:
                cursor.close()

    def _store(self, key: str, value: Any, wrapper_maxsize: int | None = None) -> None:
        pickled_value = pickle.dumps(value, protocol=self._PROTO)
        with self._lock:
            cursor = self._conn.cursor()
            try:
                # Update last_accessed time on store as well
                current_time = time.time()
                cursor.execute(
                    f"""INSERT OR REPLACE INTO {self._TABLE_NAME}
                        (key, value, last_accessed) VALUES (?, ?, ?)""",
                    (key, pickled_value, current_time),
                )
                self._conn.commit()
            finally:
                cursor.close()

    def _evict_if_needed(self, wrapper_maxsize: int | None = None) -> None:
        # If this specific wrapper has no size limit, don't evict based on size.
        if wrapper_maxsize is None:
            return

        # Since _evict_if_needed is called *after* _store in the base class,
        # current_size reflects the size *after* the new item was added.
        current_size = self._get_current_size()

        # If current size is within the wrapper's limit, no eviction needed.
        if current_size <= wrapper_maxsize:
            return

        # Calculate how many items to evict (LRU based on last_accessed)
        limit = current_size - wrapper_maxsize
        if limit <= 0:
            return

        with self._lock:
            cursor = self._conn.cursor()
            try:
                # Find the keys of the `limit` least recently accessed items
                cursor.execute(
                    f"SELECT key FROM {self._TABLE_NAME} ORDER BY last_accessed ASC LIMIT ?",
                    (limit,),
                )
                keys_to_delete = [row[0] for row in cursor.fetchall()]

                if keys_to_delete:
                    # Delete the selected keys
                    # Use placeholders correctly for a list of values
                    placeholders = ",".join("?" * len(keys_to_delete))
                    cursor.execute(
                        f"DELETE FROM {self._TABLE_NAME} WHERE key IN ({placeholders})",
                        keys_to_delete,
                    )
                    self._conn.commit()
            finally:
                cursor.close()

    def _clear(self) -> None:
        """Clear all items from the cache table."""
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute(f"DELETE FROM {self._TABLE_NAME}")
                self._conn.commit()
            finally:
                cursor.close()

    def _get_current_size(self) -> int:
        """Return the number of rows in the cache table."""
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {self._TABLE_NAME}")
                count = cursor.fetchone()[0]
                return count
            finally:
                cursor.close()

    def __del__(self) -> None:
        if hasattr(self, "_conn") and self._conn:
            self._conn.close()
