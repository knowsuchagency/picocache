from __future__ import annotations
from typing import Any

import pickle
import logging  # Import logging

from django.core.cache import caches
from django.core.cache.backends.base import BaseCache as DjangoBaseCache
from django.core.cache.backends.locmem import LocMemCache

from .base import _BaseCache, _MISSING

# Set up basic logging
logger = logging.getLogger(__name__)


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
        self._alias = alias  # Store alias for potential use in size check

    def _lookup(self, key: str) -> Any | _MISSING:
        # Fetch from cache, using our sentinel for misses
        cached_value = self._cache.get(key, default=_MISSING)

        # Explicitly check for the miss sentinel
        if cached_value is _MISSING:
            return _MISSING

        # If we found something, try to unpickle it
        try:
            value = pickle.loads(cached_value)
            self._hits += 1  # Increment hit counter *only* after successful unpickling
            return value
        except (pickle.UnpicklingError, TypeError, EOFError) as e:
            logger.warning(
                f"Failed to unpickle cache key '{key}' for alias '{self._alias}': {e}. Treating as miss."
            )
            # Optionally delete the corrupted key:
            # self._cache.delete(key)
            return _MISSING  # Treat unpickling errors as misses

    def _store(self, key: str, value: Any, wrapper_maxsize: int | None = None) -> None:
        try:
            pickled_value = pickle.dumps(value, protocol=self._PROTO)
            self._cache.set(key, pickled_value, timeout=self._default_timeout)
        except pickle.PicklingError as e:
            logger.error(f"Failed to pickle value for cache key '{key}': {e}")
            # Decide if we should raise the error or just log and skip caching
            # raise # Re-raise the error
            pass  # Logged the error, skip caching this value
        except Exception as e:
            # Catch potential errors during self._cache.set()
            logger.error(
                f"Failed to set cache key '{key}' in alias '{self._alias}': {e}"
            )
            pass  # Logged the error, skip caching

    def _evict_if_needed(self, wrapper_maxsize: int | None = None) -> None:
        # Django's cache backend handles eviction based on its own policies
        # (e.g., LRU, max entries, timeout).
        # self._default_maxsize is not directly used by this backend.
        pass

    def _clear(self) -> None:
        """Clear the configured Django cache alias."""
        self._cache.clear()

    def _get_current_size(self) -> int:
        """Return the current number of items in the cache.

        Note: This is not a standard Django Cache API feature.
        It attempts to provide a size for LocMemCache for testing purposes.
        Returns -1 if size cannot be determined.
        """
        if isinstance(self._cache, LocMemCache):
            try:
                # Access internal dictionary (implementation detail, may break)
                return len(self._cache._cache)
            except AttributeError:
                logger.warning("Could not determine size for LocMemCache.")
                return -1  # Indicate unknown size
        else:
            # Other backends (Redis, Memcached, DB) don't have a standard size API
            logger.warning(
                f"Cannot determine size for non-LocMemCache backend '{self._alias}'"
            )
            return -1  # Indicate unknown size
