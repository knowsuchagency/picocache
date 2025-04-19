"""
Tests for the `cloudcache` library.

These tests exercise both the SQLAlchemy‑backed and Redis‑backed decorators
to ensure they behave exactly like `functools.lru_cache` with respect to
hits, misses, and cache clearing.

The SQLite tests use an in‑memory database so they require no external
resources.  The Redis tests assume a Redis instance is running locally
on the default port (6379); if it is not available the tests are skipped.
"""

import pytest

from cloudcache import SQLAlchemyCache, RedisCache


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def redis_available() -> bool:
    """Return True if a Redis server is reachable on localhost:6379."""
    try:
        import redis  # imported lazily so the package remains optional

        r = redis.Redis(host="localhost", port=6379, socket_connect_timeout=0.5)
        r.ping()
        return True
    except Exception:  # pragma: no cover – any failure means Redis is not up
        return False


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def sqlite_cache():
    """
    Returns an instance of :class:`SQLAlchemyCache` bound to an in‑memory SQLite
    database so each test gets a fresh, isolated cache.
    """
    return SQLAlchemyCache("sqlite:///:memory:")


@pytest.fixture(scope="session")
def redis_cache():
    """
    Provides a Redis‑backed cache decorator if a Redis server is available;
    otherwise the fixture itself requests that the tests be skipped.
    """
    if not redis_available():
        pytest.skip("Redis server not running on localhost:6379")
    return RedisCache("redis://localhost:6379/0")


# --------------------------------------------------------------------------- #
# SQLAlchemy‑backed cache
# --------------------------------------------------------------------------- #
def test_sqlalchemy_cache_basic(sqlite_cache):
    calls = {"count": 0}

    @sqlite_cache(maxsize=32)
    def add(a: int, b: int) -> int:
        calls["count"] += 1
        return a + b

    # First invocation → cache miss
    assert add(1, 2) == 3
    # Same arguments → served from cache
    assert add(1, 2) == 3
    # Function body should have executed only once
    assert calls["count"] == 1

    info = add.cache_info()
    assert info.hits == 1
    assert info.misses == 1
    assert info.currsize == 1

    # Clearing the cache forces recomputation
    add.cache_clear()
    assert add(1, 2) == 3
    assert calls["count"] == 2


# --------------------------------------------------------------------------- #
# Redis‑backed cache (optional)
# --------------------------------------------------------------------------- #
def test_redis_cache_basic(redis_cache):
    calls = {"count": 0}

    @redis_cache(maxsize=64)
    def mul(a: int, b: int) -> int:
        calls["count"] += 1
        return a * b

    assert mul(2, 5) == 10  # miss
    assert mul(2, 5) == 10  # hit
    assert calls["count"] == 1

    info = mul.cache_info()
    assert info.hits == 1
    assert info.misses == 1
    assert info.currsize == 1

    # Clearing should invalidate the cached value
    mul.cache_clear()
    assert mul(2, 5) == 10
    assert calls["count"] == 2
