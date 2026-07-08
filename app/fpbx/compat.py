"""FusionPBX version compatibility.

The app targets FusionPBX 4.5.x (baseline) through the current release, covering
every major version in between. FusionPBX does not expose its version in the
database reliably, and the tables/columns this app uses have been stable across
the whole range — so rather than branch on a version number, we verify the
*schema capabilities* we depend on exist on the connected instance. This makes
the app resilient to any version (including newer ones) as long as the required
tables/columns are present.
"""
from __future__ import annotations

# Explicitly supported FusionPBX major versions: 4.5.x baseline → current.
SUPPORTED_VERSIONS = ["4.5", "5.0", "5.1", "5.2", "5.3", "5.4", "5.5"]
SUPPORTED_RANGE = f"FusionPBX {SUPPORTED_VERSIONS[0]}.x – {SUPPORTED_VERSIONS[-1]} (current)"

# Tables and the columns this app reads/writes. All confirmed stable across the
# supported range (verified against 4.5.x and current master schemas).
REQUIRED_SCHEMA: dict[str, set[str]] = {
    "v_domains": {"domain_uuid", "domain_name"},
    "v_extensions": {"domain_uuid", "extension"},
    "v_dialplans": {
        "dialplan_uuid",
        "app_uuid",
        "domain_uuid",
        "dialplan_context",
        "dialplan_name",
        "dialplan_number",
        "dialplan_order",
        "dialplan_enabled",
        "dialplan_xml",
        "dialplan_description",
    },
    "v_recordings": {
        "recording_uuid",
        "domain_uuid",
        "recording_name",
        "recording_filename",
        "recording_base64",
        "recording_description",
    },
}


def diff_missing(existing: dict[str, set[str]]) -> dict:
    """Compare required schema against what exists. Pure — unit-testable.

    `existing` maps table_name -> set of column names present.
    Returns {missing_tables: [...], missing_columns: ["table.col", ...]}.
    """
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for table, columns in REQUIRED_SCHEMA.items():
        present = existing.get(table)
        if not present:
            missing_tables.append(table)
            continue
        for col in sorted(columns - present):
            missing_columns.append(f"{table}.{col}")
    return {"missing_tables": sorted(missing_tables), "missing_columns": sorted(missing_columns)}


def check_schema() -> dict:
    """Query the live database for the required tables/columns.

    Returns {ok, missing_tables, missing_columns, checked_tables, supported}.
    """
    from app.fpbx.db import cursor

    tables = list(REQUIRED_SCHEMA)
    with cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (tables,),
        )
        rows = cur.fetchall()

    existing: dict[str, set[str]] = {}
    for r in rows:
        existing.setdefault(r["table_name"], set()).add(r["column_name"])

    diff = diff_missing(existing)
    return {
        "ok": not diff["missing_tables"] and not diff["missing_columns"],
        "checked_tables": tables,
        "supported": SUPPORTED_RANGE,
        **diff,
    }
