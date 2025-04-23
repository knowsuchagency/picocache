from __future__ import annotations
from typing import Any

import pickle
import time
from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    text,
    func as sqlfunc,
    Float,
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
            Column("last_accessed", Float),
        )
        self._metadata.create_all(self._engine)

    def _lookup(self, key: str):
        with self._engine.begin() as conn:
            row = conn.execute(
                select(self._table.c.value).where(self._table.c.key == key)
            ).fetchone()
            if row is None:
                return _MISSING
            update_stmt = (
                self._table.update()
                .where(self._table.c.key == key)
                .values(last_accessed=time.time())
            )
            conn.execute(update_stmt)
            self._hits += 1
            return pickle.loads(bytes.fromhex(row.value))

    def _store(self, key: str, value: Any, wrapper_maxsize: int | None = None):
        pickled = pickle.dumps(value, protocol=self._PROTO).hex()
        current_time = time.time()
        with self._engine.begin() as conn:
            dialect = self._engine.dialect.name

            if dialect == "postgresql":
                stmt = self._table.insert().values(
                    key=key, value=pickled, last_accessed=current_time
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["key"],
                    set_=dict(value=pickled, last_accessed=current_time),
                )
            elif dialect == "sqlite":
                stmt = text(
                    "INSERT OR REPLACE INTO {} (key, value, last_accessed) VALUES (:key, :value, :last_accessed)".format(
                        self._table.name
                    )
                )
            else:
                stmt = text(
                    "INSERT INTO {} (key, value, last_accessed) VALUES (:key, :value, :last_accessed) "
                    "ON CONFLICT(key) DO UPDATE SET value = :value, last_accessed = :last_accessed".format(
                        self._table.name
                    )
                )

            conn.execute(
                stmt, {"key": key, "value": pickled, "last_accessed": current_time}
            )

    def _evict_if_needed(self, wrapper_maxsize: int | None = None):
        if wrapper_maxsize is None:
            return

        current_size = self._get_current_size()

        if current_size <= wrapper_maxsize:
            return

        limit = current_size - wrapper_maxsize
        if limit <= 0:
            return

        with self._engine.begin() as conn:
            keys_to_delete_subquery = (
                select(self._table.c.key)
                .order_by(self._table.c.last_accessed.asc())
                .limit(limit)
                .subquery()
            )

            delete_stmt = self._table.delete().where(
                self._table.c.key.in_(select(keys_to_delete_subquery.c.key))
            )
            conn.execute(delete_stmt)

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
