from __future__ import annotations
from typing import Any

import pickle
from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    text,
    func as sqlfunc,
)
from sqlalchemy.engine.url import URL

from .base import _BaseCache, _MISSING


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

    def _lookup(self, key: str):
        with self._engine.begin() as conn:
            row = conn.execute(
                select(self._table.c.value).where(self._table.c.key == key)
            ).fetchone()
            if row is None:
                return _MISSING
            self._hits += 1
            return pickle.loads(bytes.fromhex(row.value))

    def _store(self, key: str, value: Any):
        pickled = pickle.dumps(value, protocol=self._PROTO).hex()
        with self._engine.begin() as conn:
            # Use dialect-specific INSERT OR IGNORE / ON CONFLICT
            dialect = self._engine.dialect.name

            if dialect == "postgresql":
                # Use ON CONFLICT DO UPDATE for PostgreSQL
                stmt = self._table.insert().values(key=key, value=pickled)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["key"], set_=dict(value=pickled)
                )
            elif dialect == "sqlite":
                # Use INSERT OR REPLACE for SQLite
                stmt = text(
                    "INSERT OR REPLACE INTO {} (key, value) VALUES (:key, :value)".format(
                        self._table.name
                    )
                )
            else:
                # Generic fallback: INSERT ON CONFLICT DO UPDATE (might not work on all DBs)
                # A truly generic solution might need INSERT then UPDATE on error.
                stmt = text(
                    "INSERT INTO {} (key, value) VALUES (:key, :value) ON CONFLICT(key) DO UPDATE SET value = :value".format(
                        self._table.name
                    )
                )

            conn.execute(stmt, {"key": key, "value": pickled})

    def _evict_if_needed(self):
        current_size = self._get_current_size()
        if self._default_maxsize is not None and current_size <= self._default_maxsize:
            return
        elif current_size <= 10_000:
            return

        limit = max(
            1000,
            (
                current_size - self._default_maxsize
                if self._default_maxsize is not None
                else 1000
            ),
        )

        with self._engine.begin() as conn:
            subquery = select(self._table.c.key).limit(limit).subquery()
            conn.execute(
                self._table.delete().where(
                    self._table.c.key.in_(select(subquery.c.key))
                )
            )

    def _clear(self) -> None:
        """Clear all items from the cache table."""
        with self._engine.begin() as conn:
            conn.execute(self._table.delete())

    def _get_current_size(self) -> int:
        """Return the number of rows in the cache table."""
        with self._engine.connect() as conn:
            count_query = select(sqlfunc.count()).select_from(self._table)
            result = conn.execute(count_query).scalar()
            return result if result is not None else 0
