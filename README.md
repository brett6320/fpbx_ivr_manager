# FusionPBX IVR / Closure Manager

Web app to schedule planned office closures on FusionPBX. Users sign in with
Microsoft Entra ID, enter a closure start/end and a normal daytime destination,
and the app generates the greeting (Google TTS), stores it as a FusionPBX
recording, builds a time-condition dialplan on a managed extension (9550–9599),
and reloads FreeSWITCH.

**Runs co-located on the FusionPBX host** — DB over localhost, recordings written
to the local FS, `reloadxml` over localhost. No remote DB/SSH exposure. (An SFTP
mode still exists for non-co-located installs; needs `pip install '.[remote]'`.)

## Why this shape

FusionPBX 4.5.x has **no REST API** for IVRs / time conditions. Config lives in
PostgreSQL; the only native runtime interconnect is FreeSWITCH `mod_xml_rpc`. So:

| Concern | Mechanism |
|---|---|
| Create/replace time condition, recording row | direct PostgreSQL, localhost (`app/fpbx/db.py`) |
| Get the `.wav` onto the FS host | local FS write (co-located); or db-base64 / SFTP (`recordings.py`) |
| Make FreeSWITCH apply changes | `mod_xml_rpc` `reloadxml`, localhost (`xmlrpc_client.py`) |
| Greeting audio | Google TTS REST, LINEAR16 @ 8 kHz mono (`tts/google_tts.py`) |
| Auth | Pluggable: **local** (default), Entra ID SSO, or LDAP (`auth/`) |
| Authz | Group-based permissions, never per-user (`auth/authz.py`) |

## Greeting composition

`app/phrases/builder.py` — greeting = **opening** + **closure description** +
**closing**. The middle sentence varies by the shape of the window:

- full single day → "closed all day Tuesday, July 7th"
- multiple full days → "closed from … through …"
- partial-day start → "closing early at …" / end → "opening late at …"
- partial window → "closed from 12:00 PM to 3:00 PM on …"
- across days with times → "from … at … until … at …"

## Run (co-located on the FusionPBX host)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env         # DB/XMLRPC point at localhost; fill Entra + Google TTS
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
.venv/bin/pytest             # phrase logic unit tests (no live services needed)
```

Deployment notes:
- Run the service as a user in the **`www-data`/`freeswitch` group** so it can
  write `FS_RECORDINGS_DIR` and read the FusionPBX DB.
- Put it behind the existing nginx (FusionPBX) on a subpath or vhost, TLS
  terminated there; keep uvicorn bound to `127.0.0.1`.
- Optional: add a FusionPBX menu item linking to `{APP_BASE_URL}` for a native feel.

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
`AUTHZ_GROUP_PERMISSIONS={"ivr-admins":["manage_closures","manage_users"]}`, then
manage users with `python manage.py add|group-add|list`.

## Dialplan model

Per closure, extension `95xx` gets a dialplan:
1. match `destination_number` `^95xx$`
2. `date-time` condition for the window
   - **inside window** → answer, play greeting, then voicemail/hangup
   - **outside window** → transfer to the normal daytime destination

Point your inbound route (or a time condition upstream) at the managed extension.

## Guardrails — we only touch what we created

The app confines itself to the managed extension pool **and** proves ownership
before any write, so it can never modify a time condition, dialplan, or
recording that a human or another app made:

- Every closure dialplan we create embeds a marker comment
  `<!-- fpbx-ivr-manager:managed -->`; recordings carry a `[fpbx-ivr-manager:managed]`
  tag in their description.
- **Update / delete refuse** on any row lacking that marker, even if the
  name/number matches (`NotManaged` → HTTP 409).
- **Auto-allocation** treats *any* dialplan on a pool number (ours or foreign)
  and any real extension as occupied — it only ever hands out a fully-free number.
- `list_closures()` surfaces only marker-carrying rows, so the UI never offers to
  edit a look-alike.

## Status / not yet done

- IVR **menu** management (multi-option trees) is not built — the current flow
  builds time-condition greetings, not branching IVR menus.
- Untested against a live FusionPBX box — DB column names verified against 4.5
  schema but confirm on your instance before production use.
