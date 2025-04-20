"""picocache — persistent drop‑in replacements for functools.lru_cache.

Exposes multiple decorators, including `SQLiteCache`, `SQLAlchemyCache`, `RedisCache`,
and `DjangoCache`, that mirror the standard library API while persisting results
in various backends so cached values survive process restarts and can be shared
across workers.
"""

from __future__ import annotations

from .django import DjangoCache
from .redis import RedisCache
from .sqlalchemy import SQLAlchemyCache
from .sqlite import SQLiteCache

__version__ = "0.6.1"


__all__ = ["SQLAlchemyCache", "RedisCache", "SQLiteCache", "DjangoCache"]
