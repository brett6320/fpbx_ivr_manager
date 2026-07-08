# FusionPBX version compatibility

The app supports **FusionPBX 4.5.x (baseline) through 5.5 (current)**, covering
every major version in between:

| Major | Supported | Notes |
|-------|-----------|-------|
| 4.5.x | ✅ baseline | Original target. |
| 5.0   | ✅ | 4.5 → 5.0 upgrade path. |
| 5.1   | ✅ | |
| 5.2   | ✅ | |
| 5.3   | ✅ | |
| 5.4   | ✅ | |
| 5.5   | ✅ current | Latest release. |

## How compatibility works — capability detection, not version branching

FusionPBX does not expose its version in the database in a reliable, stable way
(the version is a PHP constant, not a table). More importantly, **the tables and
columns this app reads/writes have been stable across the entire supported
range**, so there is no need to branch behavior on a version number.

Instead, at startup (and on demand) the app performs a **schema-capability
check**: it verifies — against the live `information_schema` — that the exact
tables and columns it depends on exist. This is version-agnostic and also keeps
the app working on any future version as long as the schema it needs is present.

### What is checked (`app/fpbx/compat.py`)

| Table | Columns required |
|-------|------------------|
| `v_domains` | `domain_uuid`, `domain_name` |
| `v_extensions` | `domain_uuid`, `extension` |
| `v_dialplans` | `dialplan_uuid`, `app_uuid`, `domain_uuid`, `dialplan_context`, `dialplan_name`, `dialplan_number`, `dialplan_order`, `dialplan_enabled`, `dialplan_xml`, `dialplan_description` |
| `v_recordings` | `recording_uuid`, `domain_uuid`, `recording_name`, `recording_filename`, `recording_base64`, `recording_description` |

All of these are present and identically named in both the 4.5.x schema and the
current (5.5) schema.

## Startup behaviour

Controlled by `FPBX_SCHEMA_CHECK`:

- `warn` (default) — run the check at startup and **log a warning** if anything
  is missing; never blocks boot (tolerant of the DB being briefly unreachable).
- `strict` — **fail startup** if the check can't run or finds missing objects.
  Use in production to catch a misconfigured/unsupported instance immediately.
- `off` — skip the check.

## On-demand check

Admins (`manage_users`) can hit **`/admin/compat`** (linked from the schedules
page) for a live JSON report:

```json
{ "ok": true, "checked_tables": ["v_domains","v_extensions","v_dialplans","v_recordings"],
  "missing_tables": [], "missing_columns": [], "supported": "FusionPBX 4.5.x – 5.5 (current)" }
```

## Version-specific notes

- **`v_dialplans.hostname`** exists in all supported versions. The app leaves it
  unset (NULL) on records it creates, so they load on **all** FreeSWITCH nodes —
  correct for single-logical-PBX deployments. (Adoption leaves an existing
  record's hostname untouched.)
- **Recording storage** differences between versions are handled by
  `FPBX_RECORDING_STORAGE` (`db` / `local` / `sftp`), not by version — both
  `recording_filename` and `recording_base64` exist throughout.
- **Time Conditions `app_uuid`** (`4b821450-926b-175a-af93-a03c441818b1`) is a
  fixed constant in FusionPBX and is unchanged across all supported versions.
- The only runtime interconnect used — FreeSWITCH `mod_xml_rpc` `reloadxml` — is
  unchanged across the range.

If FusionPBX adds a version that renames or drops one of the required columns,
the schema check surfaces it immediately (missing object + supported range in the
log / `/admin/compat`) rather than failing mid-operation.
