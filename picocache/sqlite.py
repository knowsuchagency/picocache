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

                # Update last_accessed time
                cursor.execute(
                    f"UPDATE {self._TABLE_NAME} SET last_accessed = ? WHERE key = ?",
                    (time.time(), key),
                )
                self._conn.commit()
                return pickle.loads(row[0])
            finally:
                cursor.close()

    def _store(self, key: str, value: Any) -> None:
        pickled_value = pickle.dumps(value, protocol=self._PROTO)
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute(
                    f"""INSERT OR REPLACE INTO {self._TABLE_NAME}
                        (key, value, last_accessed) VALUES (?, ?, ?)""",
                    (key, pickled_value, time.time()),
                )
                self._conn.commit()
            finally:
                cursor.close()

    def _evict_if_needed(self) -> None:
        # For simplicity and consistency with RedisCache, we won't implement
        # size-based eviction in the SQLite backend itself. The in-memory
        # lru_cache provided by _BaseCache handles maxsize limiting.
        pass

    def __del__(self) -> None:
        if hasattr(self, "_conn") and self._conn:
            self._conn.close()
