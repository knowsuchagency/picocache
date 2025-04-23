"""picocache — persistent drop‑in replacements for functools.lru_cache.

Exposes multiple decorators, including `SQLiteCache`, `SQLAlchemyCache`, `RedisCache`,
and `DjangoCache`, that mirror the standard library API while persisting results
in various backends so cached values survive process restarts and can be shared
across workers.
"""

from __future__ import annotations

from .sqlite import SQLiteCache

__version__ = "1.0.0"

__all__ = ["SQLiteCache"]  # Start with the base cache

try:
    from .django import DjangoCache

    __all__.append("DjangoCache")
except ImportError:  # pragma: no cover
    pass

try:
    from .redis import RedisCache

    __all__.append("RedisCache")
except ImportError:  # pragma: no cover
    pass

try:
    from .sqlalchemy import SQLAlchemyCache

    __all__.append("SQLAlchemyCache")
except ImportError:  # pragma: no cover
    pass
