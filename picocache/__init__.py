"""picocache — persistent drop‑in replacements for functools.lru_cache.

Exposes two decorators, `SQLAlchemyCache` and `RedisCache`, that mirror the
standard library API while persisting results in a database (via SQLAlchemy)
or in Redis so cached values survive process restarts and can be shared
across workers.
"""

from __future__ import annotations

from .redis import RedisCache
from .sqlalchemy import SQLAlchemyCache
from .sqlite import SQLiteCache

__version__ = "0.3.0"


__all__ = ["SQLAlchemyCache", "RedisCache", "SQLiteCache"]
