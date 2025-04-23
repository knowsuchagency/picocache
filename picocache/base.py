from __future__ import annotations
from typing import Any, Callable, Dict, Tuple

import functools
import pickle
import threading
from abc import ABC, abstractmethod

from .utils import _copy_metadata, _make_key


class _Missing:
    pass


_MISSING = _Missing()


# Use a standard structure similar to functools.CacheInfo
# Backends will provide the data for this.
class CacheInfo:
    def __init__(self, hits: int, misses: int, maxsize: int | None, currsize: int):
        self.hits = hits
        self.misses = misses
        self.maxsize = maxsize
        self.currsize = currsize

    def __repr__(self):
        return f"CacheInfo(hits={self.hits}, misses={self.misses}, maxsize={self.maxsize}, currsize={self.currsize})"


class _BaseCache(ABC):
    """Shared functionality for backend caches."""

    _PROTO = pickle.HIGHEST_PROTOCOL

    def __init__(
        self, *, default_maxsize: int | None = None, default_typed: bool = False
    ) -> None:
        # Note: default_maxsize is now used by cache_info and might be
        # used by specific backend implementations for eviction.
        self._default_maxsize = default_maxsize
        self._default_typed = default_typed
        # Simple in-memory counters (won't persist across instances/restarts)
        self._hits = 0
        self._misses = 0

    def __call__(
        self, maxsize: int | None | Ellipsis = ..., typed: bool | Ellipsis = ...
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:  # noqa: D401,E501
        """Return a *decorator* with caching parameters.

        ``maxsize`` might be used by specific backends for eviction policies.
        ``typed`` controls key generation. Using the ellipsis literal ``...``
        indicates *use default*."""

        if callable(maxsize) and typed is ...:  # maxsize is actually the func
            func = maxsize  # type: ignore[assignment]
            # Pass the default maxsize potentially for backend use
            return self._build_wrapper(func, self._default_maxsize, self._default_typed)

        # Store actual maxsize/typed for potential backend use
        actual_maxsize = self._default_maxsize if maxsize is ... else maxsize  # type: ignore[assignment]
        actual_typed = self._default_typed if typed is ... else typed  # type: ignore[assignment]

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            # Pass actual maxsize potentially for backend use
            return self._build_wrapper(func, actual_maxsize, actual_typed)

        return decorator

    @abstractmethod
    def _lookup(self, key: str) -> Any | _MISSING:  # noqa: D401
        """Lookup key in the persistent backend. MUST increment self._hits on hit."""
        raise NotImplementedError

    @abstractmethod
    def _store(self, key: str, value: Any) -> None:  # noqa: D401
        """Store key/value in the persistent backend."""
        raise NotImplementedError

    def _evict_if_needed(
        self, wrapper_maxsize: int | None = None
    ) -> None:  # noqa: D401
        """Perform eviction in the persistent backend if necessary."""
        # Backends are responsible for their own eviction logic (size, TTL, etc.)
        pass  # Base implementation does nothing

    @abstractmethod
    def _get_current_size(self) -> int:
        """Return the current number of items in the cache backend."""
        raise NotImplementedError

    @abstractmethod
    def _clear(self) -> None:
        """Clear all items from the cache backend."""
        raise NotImplementedError

    # --- Public wrapper methods ---

    def cache_info(self) -> CacheInfo:
        # This is the cache_info for the *cache object* itself.
        # It reports the default_maxsize configured for the object.
        return CacheInfo(
            hits=self._hits,
            misses=self._misses,
            maxsize=self._default_maxsize,  # Reports the *default* maxsize
            currsize=self._get_current_size(),
        )

    def clear(self) -> None:
        """Clear the cache and reset statistics."""
        with threading.Lock():  # Ensure atomicity for clear + stat reset
            self._clear()
            self._hits = 0
            self._misses = 0

    def _build_wrapper(
        self, func: Callable[..., Any], maxsize: int | None, typed: bool
    ) -> Callable[..., Any]:
        # maxsize is stored in self._default_maxsize, potentially used by backend
        lock = threading.RLock()  # Use RLock for potential reentrancy

        # Keep track of the specific maxsize for this wrapper instance
        # (though it's mainly used for cache_info reporting now)
        wrapper_maxsize = maxsize

        @_copy_metadata(func)
        def wrapper(*args: Any, **kwargs: Any):
            key = _make_key(args, kwargs, typed)

            with lock:
                # 1. Check persistent cache
                result = self._lookup(key)  # _lookup should increment self._hits
                if result is not _MISSING:
                    return result

                # Cache miss if we reach here
                self._misses += 1

                # 2. Cache miss: call original function
                result = func(*args, **kwargs)

                # 3. Store result in persistent cache BEFORE checking eviction
                # Pass wrapper_maxsize to _store for backend LRU logic
                self._store(key, result, wrapper_maxsize)

                # 4. Perform eviction if needed (backend-specific)
                # Called AFTER store, so current_size includes the new item.
                self._evict_if_needed(wrapper_maxsize)

                return result

        # Assign instance methods to the wrapper, but create a specific
        # cache_info function for the wrapper that knows its own maxsize.
        def wrapper_cache_info() -> CacheInfo:
            """Return cache statistics for this specific wrapper."""
            # Note: hits/misses/currsize are still shared across the main cache object
            return CacheInfo(
                hits=self._hits,
                misses=self._misses,
                maxsize=wrapper_maxsize,  # Report the wrapper's specific maxsize
                currsize=self._get_current_size(),
            )

        wrapper.cache_info = wrapper_cache_info  # type: ignore[attr-defined]
        wrapper.cache_clear = wrapper.clear = self.clear  # type: ignore[attr-defined]

        return wrapper
