from __future__ import annotations
from typing import Any, Callable

import pickle
import logging  # Import logging
import functools
import threading

from django.core.cache import caches
from django.core.cache.backends.base import BaseCache as DjangoBaseCache
from django.core.cache.backends.locmem import LocMemCache

from .base import _BaseCache, _MISSING, CacheInfo  # Import CacheInfo
from .utils import _copy_metadata, _make_key  # Import necessary utils

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
        # Django's LocMemCache (default test backend) doesn't automatically evict
        # based on a count like lru_cache. We are handling maxsize with the
        # lru_cache wrapper in _build_wrapper now.
        # Other Django backends (Redis, Memcached) might have their own size limits,
        # but they are configured outside picocache.
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

    # --- Override _build_wrapper to add functools.lru_cache ---

    def _build_wrapper(
        self, func: Callable[..., Any], maxsize: int | None, typed: bool
    ) -> Callable[..., Any]:
        lock = threading.RLock()
        wrapper_maxsize = maxsize  # The maxsize for the inner LRU cache

        @_copy_metadata(func)
        def wrapper(*args: Any, **kwargs: Any):
            # Use the same key generation as the base class
            key = _make_key(args, kwargs, typed, module_name=func.__module__)

            with lock:
                # 1. Check persistent cache (Django backend)
                result = self._lookup(key)
                if result is not _MISSING:
                    return result

                # 2. Miss in Django cache
                # Increment base miss counter *only if* lru_cache is not managing misses
                if wrapper_maxsize is None:
                    self._misses += 1

                # 3. Call the original function
                result = func(*args, **kwargs)

                # 4. Store result in persistent cache (Django backend)
                # The _store method in DjangoCache doesn't use wrapper_maxsize directly.
                self._store(key, result)  # Removed wrapper_maxsize argument

                # 5. Eviction logic is handled by the lru_cache wrapper and/or Django backend's policy
                # self._evict_if_needed is mostly a no-op here now.

                return result

        # Apply functools.lru_cache to the wrapper *if* maxsize is specified
        if wrapper_maxsize is not None:
            # functools.lru_cache requires maxsize > 0 if not None
            if wrapper_maxsize <= 0:
                logger.warning(
                    f"functools.lru_cache requires maxsize > 0, but got {wrapper_maxsize}. Skipping lru_cache."
                )
                cached_wrapper = wrapper
            else:
                cached_wrapper = functools.lru_cache(
                    maxsize=wrapper_maxsize, typed=typed
                )(wrapper)

                # --- Corrected Clear Logic ---
                # Get the base class clear method
                base_clear = super(DjangoCache, self).clear

                # Get the clear method added by lru_cache
                lru_clear = cached_wrapper.cache_clear

                # Define a function to clear both lru and base cache
                def clear_combined():
                    lru_clear()  # Clear the LRU cache
                    base_clear()  # Call the base clear method

                # Assign the combined clear to the instance method *and* the wrapper's .clear
                self.clear = clear_combined  # Monkey-patch instance clear
                # Keep lru_cache's .cache_clear, but add .clear as an alias
                cached_wrapper.clear = clear_combined  # type: ignore[attr-defined]

                # --- Corrected Cache Info Logic ---
                # Modify cache_info for the wrapper instance to report lru_cache stats
                base_cache_info = super(
                    DjangoCache, self
                ).cache_info  # Get base CacheInfo method
                # Get the original cache_info added by lru_cache BEFORE overwriting it
                original_lru_info_func = cached_wrapper.cache_info

                def info_with_lru() -> CacheInfo:
                    # Get the info from the original lru_cache wrapper's method
                    lru_info = original_lru_info_func()
                    # Get the base info (using super)
                    base_info = base_cache_info()
                    # Combine them...
                    return CacheInfo(
                        hits=lru_info.hits if lru_info else base_info.hits,
                        misses=lru_info.misses if lru_info else base_info.misses,
                        maxsize=(
                            lru_info.maxsize if lru_info else base_info.maxsize
                        ),  # Use LRU maxsize
                        currsize=(
                            lru_info.currsize if lru_info else base_info.currsize
                        ),  # Use LRU currsize
                    )

                # Assign the combined info func to the wrapper
                cached_wrapper.cache_info = info_with_lru  # type: ignore[attr-defined]

        else:  # maxsize is None, don't apply lru_cache
            cached_wrapper = wrapper

            # Assign instance methods, creating specific cache_info for the wrapper
            def wrapper_cache_info() -> CacheInfo:
                return CacheInfo(
                    hits=self._hits,
                    misses=self._misses,
                    maxsize=wrapper_maxsize,  # None in this case
                    currsize=self._get_current_size(),
                )

            cached_wrapper.cache_info = wrapper_cache_info  # type: ignore[attr-defined]
            # Assign the original base clear method to the wrapper
            base_clear = super(DjangoCache, self).clear
            cached_wrapper.cache_clear = cached_wrapper.clear = base_clear  # type: ignore[attr-defined]
            # Also ensure self.clear uses the base clear when no lru
            self.clear = base_clear

        return cached_wrapper
