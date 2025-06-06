# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PicoCache is a persistent, datastore-backed `lru_cache` for Python that provides drop-in replacements for `functools.lru_cache` while persisting cached values across process restarts and machines. The project supports multiple backends: SQLite (zero dependencies), SQLAlchemy, Redis, and Django.

## Core Commands

```bash
# Install dependencies (including all extras)
uv sync --all-extras

# Run tests
uv run pytest -v tests/

# Run specific test file
uv run pytest -v tests/test_picocache.py

# Build the package
just build

# Run the full test suite before publishing
just test

# Publish to PyPI (runs tests first)
just publish
```

## Architecture

The codebase follows a clean inheritance pattern:

1. **base.py**: Abstract `_BaseCache` class defining the core API that all cache backends must implement:
   - `_lookup()`: Retrieve from backend (must increment hits)
   - `_store()`: Save to backend
   - `_evict_if_needed()`: Handle LRU eviction
   - `_get_current_size()`: Return current cache size
   - `_clear()`: Clear all items

2. **Backend Implementations**: Each backend (sqlite.py, redis.py, sqlalchemy.py, django.py) inherits from `_BaseCache` and implements the abstract methods for their specific storage system.

3. **Key Generation**: The `_make_key()` function in utils.py creates unique cache keys from function arguments, supporting the `typed` parameter and including the module name for uniqueness across different functions.

4. **Thread Safety**: All backends use threading locks to ensure thread-safe operations.

## Testing Patterns

- Tests use pytest parametrization to run the same test suite against all backends
- Backend availability is checked at runtime (e.g., Redis tests skip if no server is available)
- Each test starts with a clean cache state
- The test suite covers basic caching, LRU eviction, cache statistics, and backend-specific features

## Important Notes

- Version is managed in `picocache/__init__.py`
- The project uses flit for building/packaging
- Optional dependencies are managed via extras: `[redis]`, `[sqlalchemy]`, `[django]`
- SQLiteCache is the only backend with zero external dependencies
