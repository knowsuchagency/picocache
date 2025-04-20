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
        default_ttl: int | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._r = (
            redis.Redis.from_url(url)
            if url
            else redis.Redis(host=host, port=port, db=db, password=password)
        )
        self._ns = namespace + ":"
        self._default_ttl = default_ttl

    def _lookup(self, key: str):
        data = self._r.get(self._ns + key)
        if data is None:
            return _MISSING
        return pickle.loads(data)

    def _store(self, key: str, value: Any):
        pickled = pickle.dumps(value, protocol=self._PROTO)
        if self._default_ttl is None:
            self._r.set(self._ns + key, pickled)
        else:
            self._r.setex(self._ns + key, self._default_ttl, pickled)

    def _evict_if_needed(self):
        pass  # Redis handles eviction (e.g., LRU, TTL)
