"""FusionPBX PostgreSQL access."""
from __future__ import annotations

from contextlib import contextmanager
from functools import cache, lru_cache

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings


@lru_cache(maxsize=1)
def _pool() -> ConnectionPool:
    conninfo = (
        f"host={settings.fpbx_db_host} port={settings.fpbx_db_port} "
        f"dbname={settings.fpbx_db_name} user={settings.fpbx_db_user} "
        f"password={settings.fpbx_db_password}"
    )
    return ConnectionPool(conninfo, min_size=1, max_size=4, kwargs={"row_factory": dict_row})


@contextmanager
def cursor():
    with _pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur
        conn.commit()


@cache
def table_columns(table: str) -> frozenset[str]:
    """Columns that exist on a table (cached). Used to stay resilient to schema
    differences across FusionPBX versions."""
    with cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
        return frozenset(r["column_name"] for r in cur.fetchall())


def insert_row(cur, table: str, values: dict) -> None:
    """INSERT, including only the columns that actually exist on `table`.

    `table` and the value keys are code constants (not user input), so the
    interpolated identifiers are safe; values are always parameterized.
    """
    cols = table_columns(table)
    data = {k: v for k, v in values.items() if k in cols}
    if not data:
        raise ValueError(f"no known columns to insert into {table}")
    keys = list(data)
    placeholders = ", ".join(["%s"] * len(keys))
    cur.execute(
        f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})",  # noqa: S608
        [data[k] for k in keys],
    )


def domain_uuid() -> str:
    """Resolve the domain_uuid for the configured FusionPBX domain."""
    with cursor() as cur:
        cur.execute(
            "SELECT domain_uuid FROM v_domains WHERE domain_name = %s",
            (settings.fpbx_domain_name,),
        )
        row = cur.fetchone()
    if not row:
        raise LookupError(f"domain {settings.fpbx_domain_name!r} not found in v_domains")
    return row["domain_uuid"]
