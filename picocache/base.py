from __future__ import annotations
from typing import Any, Callable, Dict, Tuple

import functools
import pickle
import threading

from .utils import _copy_metadata, _make_key


class _Missing:
    pass


_MISSING = _Missing()


class _BaseCache:
    """Shared functionality for SQLAlchemyCache & RedisCache."""

    _PROTO = pickle.HIGHEST_PROTOCOL

    def __init__(
        self, *, default_maxsize: int | None = 128, default_typed: bool = False
    ) -> None:
        self._default_maxsize = default_maxsize
        self._default_typed = default_typed

    def __call__(
        self, maxsize: int | None | Ellipsis = ..., typed: bool | Ellipsis = ...
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:  # noqa: D401,E501
        """Return a *decorator* with caching parameters (mirrors ``lru_cache``).

        ``maxsize``/``typed`` override defaults supplied to ``__init__``.  Using
        the ellipsis literal ``...`` indicates *use default* so the signature is
        backward compatible with plain ``functools.lru_cache`` (where the first
        positional arg may be the *function* when decorator is used without
        parentheses)."""

        if callable(maxsize) and typed is ...:  # maxsize is actually the func
            func = maxsize  # type: ignore[assignment]
            return self._build_wrapper(func, self._default_maxsize, self._default_typed)

        actual_maxsize = self._default_maxsize if maxsize is ... else maxsize  # type: ignore[assignment]
        actual_typed = self._default_typed if typed is ... else typed  # type: ignore[assignment]

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return self._build_wrapper(func, actual_maxsize, actual_typed)

        return decorator

    def _lookup(self, key: str) -> Any | _MISSING:  # noqa: D401
        raise NotImplementedError

    def _store(self, key: str, value: Any) -> None:  # noqa: D401
        raise NotImplementedError

    def _evict_if_needed(self) -> None:  # noqa: D401
        raise NotImplementedError

    def _build_wrapper(
        self, func: Callable[..., Any], maxsize: int | None, typed: bool
    ) -> Callable[..., Any]:
        memory_cache = functools.lru_cache(maxsize=maxsize, typed=typed)(func)
        lock = threading.RLock()

        @_copy_metadata(func)
        def wrapper(*args: Any, **kwargs: Any):
            key = _make_key(args, kwargs, typed)
            try:
                return memory_cache(*args, **kwargs)
            except Exception:  # noqa: BLE001 – ignore & fall‑through to datastore
                pass

            with lock:
                result = self._lookup(key)
                if result is not _MISSING:
                    memory_cache(*args, **kwargs)  # prime but ignore return
                    return result

                result = func(*args, **kwargs)
                self._evict_if_needed()
                self._store(key, result)
                memory_cache(*args, **kwargs)
                return result

        wrapper.cache_info = memory_cache.cache_info  # type: ignore[attr-defined]
        wrapper.cache_clear = memory_cache.cache_clear  # type: ignore[attr-defined]
        return wrapper
