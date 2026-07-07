"""FusionPBX PostgreSQL access."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache

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
