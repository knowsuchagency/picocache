from __future__ import annotations
from typing import Any

import pickle
from sqlalchemy import Column, MetaData, String, Table, create_engine, select, text
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
        self._size = 0

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
        if self._size <= 10_000:
            return
        with self._engine.begin() as conn:
            # Use a subquery for LIMIT in DELETE for SQLite compatibility
            subquery = select(self._table.c.key).limit(1000).subquery()
            conn.execute(
                self._table.delete().where(
                    self._table.c.key.in_(select(subquery.c.key))
                )
            )
        self._size -= 1000
