"""Tests for thread safety of picocache implementations."""

import threading
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from picocache import SQLiteCache

# Configure Django if available
try:
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                    "LOCATION": "picocache-thread-test",
                }
            },
            DATABASES={},
            INSTALLED_APPS=[],
            SECRET_KEY="fake-key-for-testing",
        )
        django.setup()
except ImportError:
    pass

# Import other caches conditionally
try:
    from picocache import RedisCache
except ImportError:
    RedisCache = None

try:
    from picocache import SQLAlchemyCache
except ImportError:
    SQLAlchemyCache = None

try:
    from picocache import DjangoCache
except ImportError:
    DjangoCache = None


def create_cache(cache_type):
    """Create a cache instance based on type."""
    if cache_type == "sqlite":
        return SQLiteCache(tempfile.mktemp(suffix=".db"))
    elif cache_type == "sqlalchemy" and SQLAlchemyCache:
        # Use a file-based database for thread safety in tests
        # In-memory SQLite databases don't work well with multi-threading
        db_file = tempfile.mktemp(suffix=".db")
        return SQLAlchemyCache(f"sqlite:///{db_file}")
    elif cache_type == "redis" and RedisCache:
        try:
            cache = RedisCache("redis://localhost:6379/15")
            # Test connection
            cache._client.ping()
            return cache
        except Exception as e:
            pytest.skip(f"Redis server not available: {e}")
    elif cache_type == "django" and DjangoCache:
        try:
            # Django cache already configured in conftest.py if available
            from django.conf import settings

            if not settings.configured:
                pytest.skip("Django not configured")
            return DjangoCache()
        except Exception as e:
            pytest.skip(f"Django cache not available: {e}")
    else:
        pytest.skip(f"{cache_type} cache not available")


@pytest.fixture(
    params=["sqlite", "sqlalchemy", "redis", "django"],
    ids=["SQLiteCache", "SQLAlchemyCache", "RedisCache", "DjangoCache"],
)
def cache(request):
    """Fixture that provides each cache implementation."""
    cache_instance = create_cache(request.param)
    yield cache_instance
    cache_instance.clear()


def test_concurrent_cache_access(cache):
    """Test that concurrent access to the same cached value is thread-safe."""
    call_count = 0
    results = []
    lock = threading.Lock()

    @cache(maxsize=128)
    def expensive_function(x):
        nonlocal call_count
        with lock:
            call_count += 1
        # Simulate expensive computation
        time.sleep(0.1)
        return x * 2

    def worker():
        # All threads call with the same argument
        result = expensive_function(5)
        results.append(result)

    # Run multiple threads concurrently
    threads = []
    for _ in range(10):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify results
    assert all(r == 10 for r in results), "All results should be 10"
    assert (
        call_count == 1
    ), "Function should only be called once despite concurrent access"

    # Check cache info
    info = expensive_function.cache_info()
    # For DjangoCache with maxsize, the stats come from functools.lru_cache
    # which might show different numbers due to its implementation
    if hasattr(cache, "_cache") and hasattr(cache._cache, "get"):
        # DjangoCache - just verify we have hits + misses = 10
        assert info.hits + info.misses == 10, "Total calls should be 10"
    else:
        assert info.hits == 9, "Should have 9 cache hits"
        assert info.misses == 1, "Should have 1 cache miss"


def test_concurrent_different_args(cache):
    """Test concurrent access with different arguments."""
    call_counts = {}
    lock = threading.Lock()

    @cache(maxsize=128)
    def compute(x):
        with lock:
            call_counts[x] = call_counts.get(x, 0) + 1
        time.sleep(0.05)
        return x * x

    def worker(n):
        return compute(n)

    # Use ThreadPoolExecutor for cleaner concurrent execution
    with ThreadPoolExecutor(max_workers=20) as executor:
        # Each number 0-9 is called twice
        futures = []
        for i in range(10):
            futures.append(executor.submit(worker, i))
            futures.append(executor.submit(worker, i))

        results = [f.result() for f in futures]

    # Each unique argument should only be computed once
    assert all(
        count == 1 for count in call_counts.values()
    ), "Each unique argument should only be computed once"

    # Verify cache statistics
    info = compute.cache_info()
    if hasattr(cache, "_cache") and hasattr(cache._cache, "get"):
        # DjangoCache - just verify we have hits + misses = 20
        assert info.hits + info.misses == 20, "Total calls should be 20"
    else:
        assert (
            info.hits == 10
        ), "Should have 10 cache hits (second call for each number)"
        assert (
            info.misses == 10
        ), "Should have 10 cache misses (first call for each number)"


def test_concurrent_clear_operations(cache):
    """Test that clear operations are thread-safe."""

    @cache(maxsize=128)
    def cached_func(x):
        return x + 1

    # Populate cache
    for i in range(10):
        cached_func(i)

    clear_count = 0
    exception_count = 0
    lock = threading.Lock()

    def worker():
        nonlocal clear_count, exception_count
        try:
            # Interleave clears with cache access
            if threading.current_thread().name.endswith(("0", "5")):
                cached_func.clear()
                with lock:
                    clear_count += 1
            else:
                # Try to access cache
                _ = cached_func(int(threading.current_thread().name[-1]))
        except Exception:
            with lock:
                exception_count += 1

    threads = []
    for i in range(10):
        t = threading.Thread(target=worker, name=f"thread-{i}")
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # No exceptions should occur
    assert (
        exception_count == 0
    ), "No exceptions should occur during concurrent clear/access"
    assert clear_count == 2, "Two threads should have cleared the cache"


def test_cache_info_consistency(cache):
    """Test that cache_info remains consistent under concurrent access."""

    @cache(maxsize=50)
    def cached_func(x):
        time.sleep(0.01)
        return x

    def worker(thread_id):
        for i in range(10):
            cached_func(thread_id * 10 + i)

    # Run multiple threads
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, i) for i in range(5)]
        for f in futures:
            f.result()

    # Verify cache info consistency
    info = cached_func.cache_info()
    assert info.hits + info.misses == 50, "Total calls should equal hits + misses"
    assert info.currsize <= info.maxsize, "Current size should not exceed maxsize"
    assert info.currsize == min(50, info.maxsize or 50), "Size should match expected"


def test_eviction_thread_safety(cache):
    """Test that LRU eviction is thread-safe."""

    @cache(maxsize=5)  # Small cache to force eviction
    def cached_func(x):
        return x * 2

    def worker(start):
        for i in range(start, start + 10):
            cached_func(i)

    # Run multiple threads that will cause eviction
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(worker, i * 10) for i in range(3)]
        for f in futures:
            f.result()

    # Verify cache state
    info = cached_func.cache_info()
    assert info.currsize == 5, "Cache size should be exactly maxsize after eviction"
    assert info.hits + info.misses == 30, "Total calls should be 30"


def test_reentrant_function_calls(cache):
    """Test thread safety with reentrant (recursive) function calls."""

    @cache(maxsize=128)
    def fibonacci(n):
        if n < 2:
            return n
        return fibonacci(n - 1) + fibonacci(n - 2)

    def worker(n):
        return fibonacci(n)

    # Multiple threads computing fibonacci numbers
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, i) for i in range(10, 15)]
        results = [f.result() for f in futures]

    # Verify results are correct
    expected = [55, 89, 144, 233, 377]  # fib(10) through fib(14)
    assert results == expected, "Fibonacci results should be correct"

    # Cache should have entries for all computed values
    info = fibonacci.cache_info()
    assert info.currsize > 0, "Cache should contain entries"
    assert info.hits > 0, "Should have cache hits from recursive calls"


def test_statistics_accuracy(cache):
    """Test that hit/miss statistics are accurate under concurrent load."""

    @cache(maxsize=100)
    def cached_func(x):
        time.sleep(0.001)
        return x

    # Track expected hits and misses
    values_seen = set()
    lock = threading.Lock()

    def worker():
        for i in range(100):
            value = i % 20  # Will cause repeated values
            with lock:
                was_cached = value in values_seen
                values_seen.add(value)

            cached_func(value)

    # Run workers
    threads = []
    for _ in range(5):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify statistics
    info = cached_func.cache_info()
    assert info.hits + info.misses == 500, "Total calls should be 500"
    assert info.currsize == 20, "Should have 20 unique values cached"
    # First 20 calls are misses, rest should be hits
    assert info.misses >= 20, "Should have at least 20 misses"
    assert info.hits == 500 - info.misses, "Hits + misses should equal total calls"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
