from __future__ import annotations
from typing import Any

import pickle

from django.core.cache import caches
from django.core.cache.backends.base import BaseCache as DjangoBaseCache

from .base import _BaseCache, _MISSING


class DjangoCache(_BaseCache):
    """Persistent cache backed by Django's cache framework."""

    def __init__(
        self,
        alias: str = "default",
        timeout: int | None = None,  # Django uses 'timeout'
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._cache: DjangoBaseCache = caches[alias]
        self._default_timeout = timeout

    def _lookup(self, key: str) -> Any | _MISSING:
        # Django's cache returns None for missing keys.
        # We need to distinguish between a stored None and a cache miss.
        # We use a sentinel _MISSING for this.
        cached_value = self._cache.get(key, default=_MISSING)
        if cached_value is _MISSING:
            return _MISSING
        try:
            # Assume value is pickled, similar to other backends
            return pickle.loads(cached_value)
        except (pickle.UnpicklingError, TypeError):
            # Handle cases where the value might not be pickled
            # or is corrupted. Treat as miss.
            return _MISSING

    def _store(self, key: str, value: Any) -> None:
        pickled_value = pickle.dumps(value, protocol=self._PROTO)
        self._cache.set(key, pickled_value, timeout=self._default_timeout)

    def _evict_if_needed(self) -> None:
        # Django's cache backend handles eviction based on its own policies
        # (e.g., LRU, max entries, timeout).
        pass
