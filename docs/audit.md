# Audit log

The app keeps an **append-only, tamper-evident audit log** of sensitive actions.
Every entry is **hash-chained** to the one before it (SHA-256 over the previous
entry's hash plus this entry's canonical payload), so deleting or altering any
earlier entry breaks the chain and is detectable.

Implemented in `app/audit.py`; viewed at **`/admin/audit`**.

## Access

Reading the log requires the **`view_audit`** permission (see
[authentication.md](authentication.md)). It is **admin-only by default** —
granted to the admin-tier groups, never to plain schedule users. The **Audit**
nav link only appears for holders of `view_audit`.

## What is recorded

Entries capture the actor (username/email), an action string, an optional target,
optional JSON details, and a UTC timestamp. Recorded actions include:

| Action | When |
|---|---|
| `schedule.delete` | a managed time condition is deleted |
| `ivr.delete` | an IVR menu is deleted |
| `phrase.delete` | a managed phrase is deleted |
| `business.save` | the business profile is saved |
| `auth_config.save` | auth settings are saved from `/admin/auth` (records which keys changed, not their values) |
| `user.create` / `user.update` / `user.delete` | local user administration |
| `user.mfa_reset` | a local user's MFA factors are reset |

Writes are **best-effort**: a failing audit backend logs an error but never blocks
or breaks the action being audited.

## Integrity check

The `/admin/audit` page runs `audit.verify()`, which recomputes the whole chain
from the first entry and reports `ok` plus the first offending sequence number if
the chain was broken. A green result means no stored entry has been modified or
removed.

## Storage

The log is a small SQLite database in the app state directory, alongside the local
users DB. Its path is set by **`AUDIT_DB`** (default `data/audit.db`). Under the
systemd deployment, point it inside the service `StateDirectory`, e.g.
`AUDIT_DB=/var/lib/fpbx-ivr-manager/audit.db`.
