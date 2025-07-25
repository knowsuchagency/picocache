from __future__ import annotations
from typing import Any

import pickle
import redis
import time

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
        self._client = (
            redis.Redis.from_url(url)
            if url
            else redis.Redis(host=host, port=port, db=db, password=password)
        )
        self._ns = namespace + ":"
        self._default_ttl = ttl
        self._lru_key = self._ns + "lru_tracker"  # Key for the sorted set tracking LRU

    def _lookup(self, key: str):
        full_key = self._ns + key
        data = self._client.get(full_key)
        if data is None:
            return _MISSING
        # Update LRU score on hit
        self._client.zadd(self._lru_key, {full_key: time.time()})
        with self._stats_lock:
            self._hits += 1
        try:
            value = pickle.loads(data)
            return value
        except Exception as e:
            # Log error appropriately in a real app
            return _MISSING

    def _store(self, key: str, value: Any, wrapper_maxsize: int | None = None):
        full_key = self._ns + key
        try:
            pickled = pickle.dumps(value, protocol=self._PROTO)
            # Use a pipeline for atomicity
            pipe = self._client.pipeline()
            if self._default_ttl is None:
                pipe.set(full_key, pickled)
            else:
                pipe.setex(full_key, self._default_ttl, pickled)
            # Add/update key in LRU tracker only if maxsize is set for the wrapper
            if wrapper_maxsize is not None and wrapper_maxsize > 0:
                pipe.zadd(self._lru_key, {full_key: time.time()})
            pipe.execute()

            # Eviction logic is now handled by _evict_if_needed

        except Exception as e:
            # Log error appropriately in a real app
            pass  # Optionally raise

    def _evict_if_needed(self, wrapper_maxsize: int | None = None):
        # Eviction logic using wrapper_maxsize
        if wrapper_maxsize is not None and wrapper_maxsize > 0:
            # Check size *after* the new item has potentially been added by _store
            current_size = self._client.zcard(self._lru_key)
            if current_size > wrapper_maxsize:
                # Number of items to remove
                num_to_remove = current_size - wrapper_maxsize
                # Get the keys of the oldest items (lowest scores)
                # We want the oldest, so range from 0 up to num_to_remove - 1
                keys_to_remove = self._client.zrange(
                    self._lru_key, 0, num_to_remove - 1
                )
                if keys_to_remove:
                    # Use pipeline to remove data keys and LRU entries
                    evict_pipe = self._client.pipeline()
                    evict_pipe.delete(*keys_to_remove)
                    evict_pipe.zrem(self._lru_key, *keys_to_remove)
                    evict_pipe.execute()

    def _clear(self) -> None:
        """Clear all keys within the namespace using SCAN."""
        cursor = 0
        match = self._ns + "*"
        while True:
            cursor, keys = self._client.scan(cursor=cursor, match=match)
            if keys:
                self._client.delete(*keys)
            if cursor == 0:
                break
        # Also delete the LRU tracker key *after* the loop
        self._client.delete(self._lru_key)

    def _get_current_size(self) -> int:
        """Return the number of keys tracked by the LRU mechanism."""
        # Always return the size of the LRU tracker set.
        # If maxsize wasn't used, the set won't be populated by _store.
        return self._client.zcard(self._lru_key)
