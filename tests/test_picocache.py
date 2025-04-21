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
    add.clear()
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
