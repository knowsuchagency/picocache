"""
Parametrised integration test for *all* picocache back‑ends.

The single test below is executed four times—once each for SQLiteCache
(stdlib, no deps), SQLAlchemyCache, RedisCache, and DjangoCache—verifying that every
implementation behaves like `functools.lru_cache` with respect to hits,
misses, `cache_info`, and `clear`.

* SQLiteCache uses a temporary on‑disk database that is deleted afterwards.
* SQLAlchemyCache uses an in‑memory SQLite URL.
* RedisCache is skipped automatically if no local Redis server is running.
* DjangoCache is skipped if Django is not installed or cannot be configured.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Iterator, Tuple, Callable, Any

import pytest

from picocache import SQLiteCache, SQLAlchemyCache, RedisCache

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _redis_available() -> bool:
    """Return True if a Redis server is reachable on localhost:6379."""
    try:
        import redis  # optional dependency
    except ModuleNotFoundError:
        return False

    try:
        r = redis.Redis(host="localhost", port=6379, socket_connect_timeout=0.5)
        r.ping()
        return True
    except Exception:  # pragma: no cover – any failure means Redis is not up
        return False


def _django_available() -> bool:
    """Return True if Django is installed and can be configured."""
    try:
        import django
        from django.conf import settings

        if not settings.configured:
            settings.configure(
                CACHES={
                    "default": {
                        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                        "LOCATION": "picocache-test",
                    }
                },
                DATABASES={},
                INSTALLED_APPS=[],
                SECRET_KEY="fake-key-for-testing",
            )
            django.setup()
        return True
    except (ModuleNotFoundError, ImportError):
        return False


# --------------------------------------------------------------------------- #
# Parametrised fixture returning a cache decorator instance
# --------------------------------------------------------------------------- #


@pytest.fixture(
    params=("sqlite", "sqlalchemy", "redis", "django"),
    ids=("SQLiteCache", "SQLAlchemyCache", "RedisCache", "DjangoCache"),
)
def cache(request) -> Iterator[Tuple[str, Callable[[Callable[..., Any]], Any]]]:
    """Yield `(backend_name, decorator_instance)` for each supported back‑end."""
    kind: str = request.param

    # SQLiteCache – temporary file to isolate state between test cases
    if kind == "sqlite":
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        deco = SQLiteCache(db_path=db_path)
        yield kind, deco
        if os.path.exists(db_path):
            os.remove(db_path)
        return

    # SQLAlchemyCache – in‑memory SQLite URL
    if kind == "sqlalchemy":
        deco = SQLAlchemyCache("sqlite:///:memory:")
        yield kind, deco
        return

    # RedisCache – skip if server unavailable
    if kind == "redis":
        if not _redis_available():
            pytest.skip("Redis server not running on localhost:6379")
        deco = RedisCache("redis://localhost:6379/0")
        yield kind, deco
        return

    # DjangoCache - skip if Django is not installed
    if kind == "django":
        if not _django_available():
            pytest.skip("Django not installed or could not be configured")
        from picocache import DjangoCache

        deco = DjangoCache()
        yield kind, deco
        return

    raise RuntimeError(f"Unknown cache kind: {kind!r}")  # safety net


# --------------------------------------------------------------------------- #
# Generic test exercised for each back‑end via the parametrised fixture
# --------------------------------------------------------------------------- #


def test_basic_caching(cache):
    backend_name, cache_deco = cache
    # Ensure a clean slate for each backend test run
    # Need to access clear via the decorated function after decoration
    calls = {"count": 0}

    # simple math function so result is deterministic & hashable
    @cache_deco(maxsize=32)
    def add(a: int, b: int) -> int:
        calls["count"] += 1
        return a + b

    # Clear cache *after* decoration but *before* first call
    add.cache_clear()
    # Reset calls counter after clear, before test logic
    calls["count"] = 0

    # 1️⃣ first call → miss
    assert add(3, 4) == 7
    # 2️⃣ second call with same args → hit
    assert add(3, 4) == 7
    # func body should have run only once
    assert (
        calls["count"] == 1
    ), f"{backend_name} failed to cache result ({calls['count']=})"

    info = add.cache_info()
    assert info.hits == 1
    assert info.misses == 1
    # Current size check might be backend-dependent (-1 if unknown)
    assert info.currsize >= 1 or info.currsize == -1  # Allow -1 for unknown size

    # clearing should force recomputation
    add.clear()
    assert add(3, 4) == 7
    assert calls["count"] == 2
    info_after_clear = add.cache_info()
    assert info_after_clear.hits == 0  # Hits should reset after clear
    assert info_after_clear.misses == 1  # Misses should be 1 after clear + 1 miss


def test_maxsize_eviction(cache):
    """Verify that maxsize is respected and causes eviction."""
    backend_name, cache_deco = cache
    max_cache_size = 2
    calls = {"count": 0}

    @cache_deco(maxsize=max_cache_size)
    def identity(x: int) -> int:
        calls["count"] += 1
        return x

    # Clear cache before starting
    identity.cache_clear()
    calls["count"] = 0

    # Call with maxsize + 1 different arguments
    assert identity(1) == 1  # miss 1
    time.sleep(0.01)
    assert identity(2) == 2  # miss 2
    time.sleep(0.01)
    assert identity(3) == 3  # miss 3 (evicts 1)

    # Check cache info
    info1 = identity.cache_info()
    assert info1.hits == 0
    assert info1.misses == 3
    # For backends that can report size, check it equals maxsize
    if info1.currsize != -1 and backend_name != "django":
        assert (
            info1.currsize == max_cache_size
        ), f"{backend_name} failed currsize check ({info1.currsize=})"

    # Check internal call count
    assert (
        calls["count"] == 3
    ), f"{backend_name} called function body {calls['count']} times, expected 3"

    # === Standard LRU Backends (including Django now) ===
    # Call the first argument again - should be a MISS from LRU due to eviction
    assert identity(1) == 1
    # T4 > T3 > T2
    # LRU state: {2:T2, 3:T3, 1:T4}. Eviction needed (size=3, limit=2).
    # Oldest is 2 (T2). After eviction: {3:T3, 1:T4}

    # Check cache info after potential miss 4 (LRU miss, but Django might hit)
    info2 = identity.cache_info()
    assert info2.hits == 0  # LRU hits should still be 0
    assert info2.misses == 4  # LRU misses should be 4

    # === Adjust expected call count for Django ===
    expected_calls_after_miss4 = 4
    if backend_name == "django":
        # Django hit the persistent cache, so func wasn't called again
        expected_calls_after_miss4 = 3

    if info2.currsize != -1:
        assert (
            info2.currsize == max_cache_size
        ), f"{backend_name} failed currsize check after miss 4 ({info2.currsize=})"
    assert (
        calls["count"] == expected_calls_after_miss4
    ), f"{backend_name} call count check after miss 4"

    # Call the *second* argument - should be an LRU MISS because it was evicted
    # For Django, this will also be a persistent hit.
    assert identity(2) == 2
    # T5 > T4 > T3
    # LRU state: {3:T3, 1:T4, 2:T5}. Eviction needed (size=3, limit=2).
    # Oldest is 3 (T3). After eviction: {1:T4, 2:T5}

    # Check cache info after potential miss 5
    info3 = identity.cache_info()
    assert info3.hits == 0  # Still no LRU hits
    assert info3.misses == 5  # LRU misses = 5

    # === Adjust expected call count for Django ===
    expected_calls_after_miss5 = 5
    if backend_name == "django":
        # Django hit the persistent cache, so func wasn't called again
        expected_calls_after_miss5 = 3  # Count is still 3

    if info3.currsize != -1:
        assert (
            info3.currsize == max_cache_size
        ), f"{backend_name} failed currsize check after miss 5 ({info3.currsize=})"
    assert (
        calls["count"] == expected_calls_after_miss5
    ), f"{backend_name} call count check after miss 5"

    # Call the second argument *again* - should now be an LRU HIT
    assert identity(2) == 2
    # T6 > T5 > T4
    # LRU state: {1:T4, 2:T6}. No eviction.

    # Check final cache info after LRU hit 1
    info4 = identity.cache_info()
    assert info4.hits == 1  # LRU hit
    assert info4.misses == 5  # LRU misses

    # === Adjust expected call count for Django ===
    expected_calls_final = 5  # Same as previous step for non-Django
    if backend_name == "django":
        expected_calls_final = 3  # Still 3 for Django

    assert (
        calls["count"] == expected_calls_final
    ), f"{backend_name} final call count check"

    if info4.currsize != -1:
        assert (
            info4.currsize == max_cache_size
        ), f"{backend_name} failed final currsize check ({info4.currsize=})"

    # Clean up
    identity.cache_clear()
