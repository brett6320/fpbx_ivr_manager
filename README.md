# FusionPBX IVR Manager

[![CI](https://github.com/brett6320/fpbx_ivr_manager/actions/workflows/ci.yml/badge.svg)](https://github.com/brett6320/fpbx_ivr_manager/actions/workflows/ci.yml)
[![CodeQL](https://github.com/brett6320/fpbx_ivr_manager/actions/workflows/codeql.yml/badge.svg)](https://github.com/brett6320/fpbx_ivr_manager/actions/workflows/codeql.yml)
[![Release](https://github.com/brett6320/fpbx_ivr_manager/actions/workflows/release.yml/badge.svg)](https://github.com/brett6320/fpbx_ivr_manager/actions/workflows/release.yml)
[![Latest release](https://img.shields.io/github/v/release/brett6320/fpbx_ivr_manager?logo=github)](https://github.com/brett6320/fpbx_ivr_manager/releases)

Web app to schedule planned IVR schedules on FusionPBX. Users sign in with
Microsoft Entra ID, enter a schedule start/end and a normal daytime destination,
and the app generates the greeting (Google TTS), stores it as a FusionPBX
recording, builds a time-condition dialplan on a managed extension (9550–9599),
and reloads FreeSWITCH.

**Runs co-located on the FusionPBX host** — DB over localhost, recordings written
to the local FS, `reloadxml` over localhost. No remote DB/SSH exposure. (An SFTP
mode still exists for non-co-located installs; needs `pip install '.[remote]'`.)

**Supported FusionPBX: 4.5.x through 5.5 (current)** — every major version in
between. Compatibility is enforced by a startup schema-capability check, not
version branching; see [docs/compatibility.md](docs/compatibility.md).

## Why this shape

FusionPBX has **no REST API** for IVRs / time conditions. Config lives in
PostgreSQL; the only native runtime interconnect is FreeSWITCH `mod_xml_rpc`. So:

| Concern | Mechanism |
|---|---|
| Create/replace time condition, recording row | direct PostgreSQL, localhost (`app/fpbx/db.py`) |
| Get the `.wav` onto the FS host | local FS write (co-located); or db-base64 / SFTP (`recordings.py`) |
| Make FreeSWITCH apply changes | `mod_xml_rpc` `reloadxml`, localhost (`xmlrpc_client.py`) |
| Greeting audio | Google TTS REST (service-account auth), LINEAR16 @ 8 kHz mono (`tts/google_tts.py`) |
| Auth | Pluggable: **local** (default), Entra ID SSO, or LDAP (`auth/`) |
| Authz | Group-based permissions, never per-user (`auth/authz.py`) |

## Greeting composition

`app/phrases/builder.py` — greeting = **opening** + **schedule description** +
**closing**. The middle sentence varies by the shape of the window:

- full single day → "closed all day Tuesday, July 7th"
- multiple full days → "closed from … through …"
- partial-day start → "closing early at …" / end → "opening late at …"
- partial window → "closed from 12:00 PM to 3:00 PM on …"
- across days with times → "from … at … until … at …"

## Run (local dev)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env         # DB/XMLRPC point at localhost; fill auth + Google TTS
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
.venv/bin/pytest             # unit tests (no live services needed)
```

## Deploy

Two supported paths, both least-privilege — see **[docs/deployment.md](docs/deployment.md)**:

- **Docker** — `Dockerfile` + `compose.yaml` (non-root, read-only rootfs, all caps
  dropped, loopback-only). Pull `ghcr.io/brett6320/fpbx_ivr_manager:latest`
  (multi-arch amd64/arm64) or build locally.
- **Standalone (systemd)** — hardened unit in `deploy/`, dedicated `ivrmgr` user.

Both use a scoped PostgreSQL role (`sql/least_privilege_role.sql`) instead of the
FusionPBX owner, sit behind nginx TLS (`deploy/nginx-fpbx-ivr-manager.conf`), and
prefer `FPBX_RECORDING_STORAGE=db` to avoid host filesystem access. Images and
versioned releases are published to GHCR on every merge to `main`.

## Authentication & authorization

Sign-in backend is pluggable via `AUTH_BACKEND` = `local` (default), `entra`, or
`ldap`. **Authorization is group-based** — permissions attach to groups, never to
individual users. Full setup guides:

- [docs/authentication.md](docs/authentication.md) — model, local users, group→permission mapping
- [docs/entra-sso.md](docs/entra-sso.md) — Entra ID app registration, groups claim, troubleshooting
- [docs/ldap.md](docs/ldap.md) — LDAP/AD bind, group resolution, TLS
- [docs/mfa.md](docs/mfa.md) — MFA (passkey/TOTP) for local admins + break-glass access

**Local admins are retained even under an external IdP** and must complete MFA
(passkey or TOTP). Break-glass local-admin login is always at `/auth/local`.

Quick start (local backend): set `LOCAL_ADMIN_USER` / `LOCAL_ADMIN_PASSWORD` and a
mapping like
`AUTHZ_GROUP_PERMISSIONS={"ivr-admins":["manage_schedules","manage_users"]}`, then
manage users with `python manage.py add|group-add|list`.

## Dialplan model

Per schedule, extension `95xx` gets a dialplan:
1. match `destination_number` `^95xx$`
2. `date-time` condition for the window
   - **inside window** → answer, play greeting, then voicemail/hangup
   - **outside window** → transfer to the normal daytime destination

Point your inbound route (or a time condition upstream) at the managed extension.

### Scope & native Time Conditions

Scope is defined by our **ownership marker** — the app manages only records it
created, plus any existing time conditions an **admin manually adopts**. New
schedules auto-allocate from the **managed extension pool (9550–9599)**; adopted
ones keep their own extension (which may be outside the pool).

**Manual adoption (admins only, `manage_users`).** `/admin/adopt` lists existing
FusionPBX Time Conditions in the domain not yet managed by the app (by
`app_uuid`). Adopting one converts it into an app-managed schedule **on its
existing extension**, stamping our marker and **replacing its routing** with the
schedule model. This is a deliberate, per-record admin action — the app never
auto-adopts. Identity is the marker + extension number, so adopted records
(arbitrary names, any number) list/edit/delete like pool-created ones.

Records are stamped with FusionPBX's Time Conditions `app_uuid`
(`4b821450-926b-175a-af93-a03c441818b1`), so they appear as **native Time
Conditions** in the FusionPBX GUI. Because the app writes `dialplan_xml` directly
(not `v_dialplan_details`), **manage these through this app** — editing one in the
FusionPBX Time Conditions GUI regenerates its XML from absent details and strips
the marker, after which the app treats it as foreign and refuses to touch it
(fail-safe, never destructive).

## Guardrails — we only touch what we created

The app confines itself to the managed extension pool **and** proves ownership
before any write, so it can never modify a time condition, dialplan, or
recording that a human or another app made:

- Every schedule dialplan we create embeds a marker comment
  `<!-- fpbx-ivr-manager:managed -->`; recordings carry a `[fpbx-ivr-manager:managed]`
  tag in their description.
- **Update / delete refuse** on any row lacking that marker, even if the
  name/number matches (`NotManaged` → HTTP 409).
- **Auto-allocation** treats *any* dialplan on a pool number (ours or foreign)
  and any real extension as occupied — it only ever hands out a fully-free number.
- `list_schedules()` surfaces only marker-carrying rows, so the UI never offers to
  edit a look-alike.

## Business profile & prompt placeholders

Admins set a **business name** and reusable, named **templates** (a generic
greeting, a closing line, hours, etc.) at `/admin/business`. Prompts reference
`{business_name}` and `{<template>}` — plugged in when the greeting audio is
generated, with nested templates resolved recursively. See
[docs/business.md](docs/business.md).

## Export & import

Admins can export the managed items (business profile, schedules, IVRs) and
import them elsewhere. Import is **reviewed item by item** — each item is editable
and nothing commits until you check it in. See [docs/portability.md](docs/portability.md).

## IVR menus

Beyond time-condition greetings, the app builds native FusionPBX **IVR menus** —
a greeting (TTS or existing recording), per-digit caller options routing to real
FusionPBX destinations (extensions, ring groups, voicemail boxes), and a timeout
destination. The **one-flow wizard at `/flow/new`** builds the whole chain in one
step — inbound route → time condition → IVR → option/timeout — creating the
inbound route (Destinations app), the time condition, and the IVR together. See
[docs/ivr.md](docs/ivr.md). Manage IVRs at `/ivrs`.

## Status / not yet done

- Multi-level / nested IVR trees (sub-menus) — the current IVR is a single menu.
- Untested against a live FusionPBX box — DB column names verified against the
  4.5.x and current (5.5) schemas, and checked at startup (see
  [docs/compatibility.md](docs/compatibility.md)); confirm on your instance
  before production use.
