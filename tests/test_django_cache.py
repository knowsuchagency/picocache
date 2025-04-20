"""
Test for DjangoCache backend without a full Django project.

This test configures Django settings in-memory using settings.configure()
to validate that DjangoCache works properly without requiring a full
Django project setup.
"""

from __future__ import annotations

import os
import django
from django.conf import settings
import pytest
import time

# Configure Django settings before importing DjangoCache
if not settings.configured:
    settings.configure(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "picocache-test",
            }
        },
        DATABASES={},  # Empty dict to avoid warnings about missing db config
        INSTALLED_APPS=[],
        SECRET_KEY="fake-key-for-testing",
    )
    django.setup()

from picocache import DjangoCache


# Fixture to provide a DjangoCache instance
@pytest.fixture
def cache():
    # Return the class itself or a default instance if needed by tests
    # For tests that need specific setup like timeout, they instantiate directly
    return DjangoCache()


def test_django_cache_basic(cache):
    """Test that DjangoCache works correctly with basic operations."""
    calls = {"count": 0}

    @cache
    def add(a: int, b: int) -> int:
        calls["count"] += 1
        return a + b

    add.cache_clear()
    calls["count"] = 0

    # First call - should be a miss
    assert add(3, 4) == 7
    # Second call with same args - should be a hit
    assert add(3, 4) == 7
    # Function body should have run only once
    assert calls["count"] == 1, "DjangoCache failed to cache result"

    # Check cache stats
    info = add.cache_info()
    assert info.hits == 1
    assert info.misses == 1
    assert info.currsize == 1

    # Clearing should force recomputation
    add.cache_clear()
    assert add(3, 4) == 7
    assert calls["count"] == 2
    info_after_clear = add.cache_info()
    assert info_after_clear.hits == 0
    assert info_after_clear.misses == 1

    # Different arguments should cause a cache miss
    assert add(5, 6) == 11
    assert calls["count"] == 3
    info_after_diff_args = add.cache_info()
    assert info_after_diff_args.hits == 0
    assert info_after_diff_args.misses == 2


def test_django_cache_custom_timeout():
    """Test DjangoCache with a specific timeout."""
    cache_deco = DjangoCache(timeout=1)
    calls = {"count": 0}

    @cache_deco
    def add(a: int, b: int) -> int:
        calls["count"] += 1
        return a + b

    # Clear before first call
    add.cache_clear()
    calls["count"] = 0

    # 1. First call (Miss)
    assert add(1, 2) == 3
    assert calls["count"] == 1

    # 2. Second call (Hit - within timeout)
    assert add(1, 2) == 3
    assert calls["count"] == 1
    info = add.cache_info()
    assert info.hits == 1
    assert info.misses == 1

    # 3. Wait for timeout to expire
    time.sleep(1.5)

    # 4. Third call (Miss - after timeout)
    assert add(1, 2) == 3
    assert calls["count"] == 2
    info_after_timeout = add.cache_info()
    assert info_after_timeout.hits == 1
    assert info_after_timeout.misses == 2


def test_django_cache_custom_alias():
    """Test that DjangoCache works with a non-default cache alias."""
    # Configure a second cache
    settings.CACHES["secondary"] = {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "picocache-secondary",
    }

    cache_deco = DjangoCache(alias="secondary")
    calls = {"count": 0}

    @cache_deco(maxsize=32)
    def add(a: int, b: int) -> int:
        calls["count"] += 1
        return a + b

    add.cache_clear()
    calls["count"] = 0

    # Check basic caching works
    assert add(3, 4) == 7
    assert add(3, 4) == 7
    assert calls["count"] == 1
    info = add.cache_info()
    assert info.hits == 1
    assert info.misses == 1
    assert info.currsize == 1
