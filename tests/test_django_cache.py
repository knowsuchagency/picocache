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


def test_django_cache_basic():
    """Test that DjangoCache works correctly with basic operations."""
    cache = DjangoCache()
    calls = {"count": 0}

    @cache
    def add(a: int, b: int) -> int:
        calls["count"] += 1
        return a + b

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

    # Different arguments should cause a cache miss
    assert add(5, 6) == 11
    assert calls["count"] == 3


def test_django_cache_custom_timeout():
    """Test that DjangoCache works with custom timeout."""
    # Using a very short timeout for testing
    cache_deco = DjangoCache(timeout=1)
    calls = {"count": 0}

    @cache_deco(maxsize=32)
    def add(a: int, b: int) -> int:
        calls["count"] += 1
        return a + b

    # First call
    assert add(3, 4) == 7
    assert calls["count"] == 1

    # Still cached
    assert add(3, 4) == 7
    assert calls["count"] == 1

    # Sleep to let cache expire
    import time

    time.sleep(1.1)  # Slightly longer than timeout

    # Should be a miss now
    assert add(3, 4) == 7
    assert calls["count"] == 2


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

    # Check basic caching works
    assert add(3, 4) == 7
    assert add(3, 4) == 7
    assert calls["count"] == 1
