from __future__ import annotations
from typing import Any

import pickle
import redis

from .base import _BaseCache, _MISSING


class RedisCache(_BaseCache):
    """Persistent cache backed by Redis."""

    def __init__(
        self,
        url: str | None = None,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        namespace: str = "picocache",
        ttl: int | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._r = (
            redis.Redis.from_url(url)
            if url
            else redis.Redis(host=host, port=port, db=db, password=password)
        )
        self._ns = namespace + ":"
        self._default_ttl = ttl

    def _lookup(self, key: str):
        full_key = self._ns + key
        data = self._r.get(full_key)
        if data is None:
            return _MISSING
        self._hits += 1  # Increment hit counter
        try:
            value = pickle.loads(data)
            return value
        except Exception as e:
            # Log error appropriately in a real app
            return _MISSING

    def _store(self, key: str, value: Any):
        full_key = self._ns + key
        try:
            pickled = pickle.dumps(value, protocol=self._PROTO)
            if self._default_ttl is None:
                self._r.set(full_key, pickled)
            else:
                self._r.setex(full_key, self._default_ttl, pickled)
        except Exception as e:
            # Log error appropriately in a real app
            pass  # Optionally raise

    def _evict_if_needed(self):
        # Redis handles eviction based on its configuration (e.g., LRU, TTL).
        # self._default_maxsize is not directly used by this backend for eviction.
        pass

    def _clear(self) -> None:
        """Clear all keys within the namespace using SCAN."""
        cursor = 0
        match = self._ns + "*"
        while True:
            cursor, keys = self._r.scan(cursor=cursor, match=match)
            if keys:
                self._r.delete(*keys)
            if cursor == 0:
                break

    def _get_current_size(self) -> int:
        """Return the number of keys within the namespace using SCAN."""
        count = 0
        cursor = 0
        match = self._ns + "*"
        while True:
            cursor, keys = self._r.scan(cursor=cursor, match=match)
            count += len(keys)
            if cursor == 0:
                break
        return count
