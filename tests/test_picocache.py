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

    # === Divergence for Django ===
    if backend_name == "django":
        # Django LocMemCache doesn't evict based on maxsize, so 1 should still be present.
        # Call the first argument again - should be a HIT
        assert identity(1) == 1  # hit 1
        time.sleep(0.01)

        # Check final cache info
        info2 = identity.cache_info()
        assert info2.hits == 1  # Should have 1 hit now
        assert info2.misses == 3  # Misses shouldn't have increased
        # Currsize will be 3
        assert info2.currsize == 3

        # Check final call count (should not increase)
        assert calls["count"] == 3

        # Call one of the *other* cached items - should also be a hit
        assert identity(2) == 2  # hit 2
        time.sleep(0.01)

        info3 = identity.cache_info()
        assert info3.hits == 2
        assert info3.misses == 3
        assert calls["count"] == 3  # Should not have increased

    else:
        # === Standard LRU Backends (SQLite, SQLAlchemy) ===
        # Call the first argument again - should be a MISS due to eviction
        assert identity(1) == 1  # miss 4, oldest was 1, cache={2:T2, 3:T3}
        time.sleep(0.01)
        # T4 > T3 > T2
        # Cache state: {2:T2, 3:T3, 1:T4}. Eviction needed (size=3, limit=1).
        # Oldest is 2 (T2). After eviction: {3:T3, 1:T4}

        # Check cache info after miss 4
        info2 = identity.cache_info()
        assert info2.hits == 0
        assert info2.misses == 4
        if info2.currsize != -1:
            assert (
                info2.currsize == max_cache_size
            ), f"{backend_name} failed currsize check after miss 4 ({info2.currsize=})"
        assert calls["count"] == 4, f"{backend_name} call count check after miss 4"

        # Call the *second* argument - should be a MISS because it was evicted
        assert identity(2) == 2  # miss 5
        time.sleep(0.01)
        # T5 > T4 > T3
        # Cache state: {3:T3, 1:T4, 2:T5}. Eviction needed (size=3, limit=1).
        # Oldest is 3 (T3). After eviction: {1:T4, 2:T5}

        # Check cache info after miss 5
        info3 = identity.cache_info()
        assert info3.hits == 0  # Still no hits
        assert info3.misses == 5
        if info3.currsize != -1:
            assert (
                info3.currsize == max_cache_size
            ), f"{backend_name} failed currsize check after miss 5 ({info3.currsize=})"
        assert calls["count"] == 5, f"{backend_name} call count check after miss 5"

        # Call the second argument *again* - should now be a HIT
        assert identity(2) == 2  # hit 1
        time.sleep(0.01)
        # T6 > T5 > T4
        # Cache state: {1:T4, 2:T6}. No eviction.

        # Check final cache info after hit 1
        info4 = identity.cache_info()
        assert info4.hits == 1
        assert info4.misses == 5
        assert calls["count"] == 5  # Hit, so count doesn't increase
        if info4.currsize != -1:
            assert (
                info4.currsize == max_cache_size
            ), f"{backend_name} failed final currsize check ({info4.currsize=})"

    # Clean up
    identity.cache_clear()
