"""picocache.py ‑ persistent decorators mirroring ``functools.lru_cache``
=======================================================================

**Goal** – feel *identical* to ``functools.lru_cache`` for users, while storing
results in either a SQL database (via SQLAlchemy) or Redis.  Therefore:

* **Connection details → ``__init__``** – the class instance is initialised with
  everything required to reach the datastore.
* **Caching parameters → ``__call__``** – when you *apply* the instance as a
  decorator you may pass ``maxsize`` / ``typed`` exactly like
  ``lru_cache``::

    from picocache import SQLAlchemyCache, RedisCache

    # SQL example – build the decorator instance with connection info …
    sql_cache = SQLAlchemyCache(url="sqlite:///cache.db")

    # …then use it with familiar lru‑style knobs
    @sql_cache(maxsize=512, typed=True)
    def fib(n: int) -> int:
        return n if n < 2 else fib(n‑1) + fib(n‑2)

    # Redis example – in one line
    @RedisCache(host="localhost", port=6379)(maxsize=256)
    def slow_value(x: str):
        return x.upper()

The wrapped function gets ``cache_info`` and ``cache_clear`` helpers identical
in spirit to those from ``functools``.

-------------------------------------------------------------------------------
                               Implementation
-------------------------------------------------------------------------------
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import pickle
import threading
import time
from typing import Any, Callable, Dict, Hashable, Tuple

__version__ = "0.1.0"

# ----------------------------- key utilities ---------------------------------


def _make_key(args: Tuple[Any, ...], kwargs: Dict[str, Any], typed: bool) -> str:
    """Create a stable hashable key from call args/kwargs (mimics internal
    ``functools._make_key`` but returns hex digest for external storage)."""
    key_parts: Tuple[Any, ...] = args
    if kwargs:
        # Convert kwargs to a tuple sorted by key to achieve stable ordering
        key_parts += (object(),)  # separator to avoid collisions
        for item in sorted(kwargs.items()):
            key_parts += item
    if typed:
        key_parts += tuple(type(v) for v in args)
        if kwargs:
            key_parts += tuple(type(v) for _, v in sorted(kwargs.items()))
    pickled = pickle.dumps(key_parts, protocol=pickle.HIGHEST_PROTOCOL)
    return hashlib.sha256(pickled).hexdigest()


# ------------------------------ base class ------------------------------------


class _BaseCache:
    """Shared functionality for SQLAlchemyCache & RedisCache."""

    #: default pickle protocol – override if you want different serialisation
    _PROTO = pickle.HIGHEST_PROTOCOL

    def __init__(
        self, *, default_maxsize: int | None = 128, default_typed: bool = False
    ) -> None:
        self._default_maxsize = default_maxsize
        self._default_typed = default_typed

    # API façade --------------------------------------------------------------
    def __call__(
        self, maxsize: int | None | Ellipsis = ..., typed: bool | Ellipsis = ...
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:  # noqa: D401,E501
        """Return a *decorator* with caching parameters (mirrors ``lru_cache``).

        ``maxsize``/``typed`` override defaults supplied to ``__init__``.  Using
        the ellipsis literal ``...`` indicates *use default* so the signature is
        backward compatible with plain ``functools.lru_cache`` (where the first
        positional arg may be the *function* when decorator is used without
        parentheses)."""

        # Support usage without explicit parentheses – e.g. ``@cache``
        if callable(maxsize) and typed is ...:  # maxsize is actually the func
            func = maxsize  # type: ignore[assignment]
            return self._build_wrapper(func, self._default_maxsize, self._default_typed)

        # Otherwise user provided parameters explicitly
        actual_maxsize = self._default_maxsize if maxsize is ... else maxsize  # type: ignore[assignment]
        actual_typed = self._default_typed if typed is ... else typed  # type: ignore[assignment]

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return self._build_wrapper(func, actual_maxsize, actual_typed)

        return decorator

    # ---------------------------------------------------------------------
    # Concrete subclasses must implement the following three abstract helpers
    # ---------------------------------------------------------------------
    def _lookup(self, key: str) -> Any | _MISSING:  # noqa: D401
        raise NotImplementedError

    def _store(self, key: str, value: Any) -> None:  # noqa: D401
        raise NotImplementedError

    def _evict_if_needed(self) -> None:  # noqa: D401
        raise NotImplementedError

    # ------------------------ wrapper factory ------------------------------
    def _build_wrapper(
        self, func: Callable[..., Any], maxsize: int | None, typed: bool
    ) -> Callable[..., Any]:
        # In‑process LRU front‑end for speed (delegates size handling to functools) –
        # this also means we get free ``cache_info``/``cache_clear`` helpers.
        memory_cache = functools.lru_cache(maxsize=maxsize, typed=typed)(func)
        lock = threading.RLock()

        @_copy_metadata(func)
        def wrapper(*args: Any, **kwargs: Any):
            key = _make_key(args, kwargs, typed)
            # 1️⃣ Fast path: memory cache
            try:
                return memory_cache(*args, **kwargs)
            except Exception:  # noqa: BLE001 – ignore & fall‑through to datastore
                pass

            with lock:
                # 2️⃣ Check external store
                result = self._lookup(key)
                if result is not _MISSING:
                    # Populate memory cache so subsequent hits are fast
                    memory_cache(*args, **kwargs)  # prime but ignore return
                    return result

                # 3️⃣ Compute & persist
                result = func(*args, **kwargs)
                self._evict_if_needed()
                self._store(key, result)
                # Prime memory cache
                memory_cache(*args, **kwargs)
                return result

        # expose helpers matching functools interface
        wrapper.cache_info = memory_cache.cache_info  # type: ignore[attr-defined]
        wrapper.cache_clear = memory_cache.cache_clear  # type: ignore[attr-defined]
        return wrapper


# sentinel
class _Missing:
    pass


_MISSING = _Missing()

# ----------------------- SQLAlchemy implementation ---------------------------

from sqlalchemy import Column, MetaData, String, Table, create_engine, select, text
from sqlalchemy.exc import OperationalError


class SQLAlchemyCache(_BaseCache):
    """Persistent cache backed by any SQLAlchemy‑supported database."""

    def __init__(
        self,
        url: str | None = None,
        drivername: str | None = None,
        username: str | None = None,
        password: str | None = None,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        table_name: str = "picocache",
        echo: bool = False,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        if url is None:
            if drivername is None:
                raise ValueError("Either *url* or *drivername* must be provided")
            from sqlalchemy.engine.url import URL

            url = str(
                URL.create(
                    drivername,
                    username=username,
                    password=password,
                    host=host,
                    port=port,
                    database=database,
                )
            )
        self._engine = create_engine(url, echo=echo, future=True)
        self._metadata = MetaData()
        self._table = Table(
            table_name,
            self._metadata,
            Column("key", String(64), primary_key=True),
            Column("value", String),
        )
        self._metadata.create_all(self._engine)
        self._size = 0  # track count for naïve eviction

    # datastore hooks -------------------------------------------------------
    def _lookup(self, key: str):
        with self._engine.begin() as conn:
            row = conn.execute(
                select(self._table.c.value).where(self._table.c.key == key)
            ).fetchone()
            if row is None:
                return _MISSING
            return pickle.loads(bytes.fromhex(row.value))

    def _store(self, key: str, value: Any):
        pickled = pickle.dumps(value, protocol=self._PROTO).hex()
        with self._engine.begin() as conn:
            conn.execute(
                (
                    self._table.insert()
                    .values(key=key, value=pickled)
                    .on_conflict_do_nothing(index_elements=["key"])
                    if self._engine.dialect.name == "postgresql"
                    else text(
                        "INSERT OR IGNORE INTO {} (key, value) VALUES (:key, :value)".format(
                            self._table.name
                        )
                    )
                ),
                {"key": key, "value": pickled},
            )
        self._size += 1

    def _evict_if_needed(self):
        # Simple size cap: if table rows > 10_000 delete oldest (timestamp not stored
        # so we drop random).  Users needing more control should manage externally.
        if self._size <= 10_000:
            return
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    f"DELETE FROM {self._table.name} WHERE rowid IN (SELECT rowid FROM {self._table.name} LIMIT 1000)"
                )
            )
        self._size -= 1000


# ------------------------------ Redis backend --------------------------------

import redis


class RedisCache(_BaseCache):
    """Persistent cache backed by Redis."""

    def __init__(
        self,
        url: str | None = None,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        namespace: str = "picocache",
        default_ttl: int | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._r = (
            redis.Redis.from_url(url)
            if url
            else redis.Redis(host=host, port=port, db=db, password=password)
        )
        self._ns = namespace + ":"
        self._default_ttl = default_ttl

    # datastore hooks -------------------------------------------------------
    def _lookup(self, key: str):
        data = self._r.get(self._ns + key)
        if data is None:
            return _MISSING
        return pickle.loads(data)

    def _store(self, key: str, value: Any):
        pickled = pickle.dumps(value, protocol=self._PROTO)
        if self._default_ttl is None:
            self._r.set(self._ns + key, pickled)
        else:
            self._r.setex(self._ns + key, self._default_ttl, pickled)

    def _evict_if_needed(self):
        # rely on Redis's own eviction policy; nothing to do here
        pass


# ------------------------------ helpers --------------------------------------


def _copy_metadata(src_func: Callable[..., Any]):
    """Return a ``functools.wraps`` decorator pre‑configured for *src_func*."""
    return functools.wraps(
        src_func,
        assigned=functools.WRAPPER_ASSIGNMENTS + ("__annotations__",),
        updated=(),
    )


# ---------------------------------- eof --------------------------------------
