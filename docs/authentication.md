# Authentication & Authorization

The app separates **authentication** (who are you?) from **authorization**
(what may you do?).

- **Authentication** is handled by a pluggable backend selected with
  `AUTH_BACKEND`: `local` (default), `entra` (Microsoft Entra ID SSO),
  `ldap`, or `fpbx` (the FusionPBX user database — see [fpbx-auth.md](fpbx-auth.md)).
  Each backend produces a session identity that includes the user's
  **group memberships**.
- **Authorization** is entirely **group-based**. Permissions are attached to
  groups via `AUTHZ_GROUP_PERMISSIONS`; a user's effective permissions are the
  union of the permissions of the groups they belong to. **Permissions are never
  bound to individual users** — there is no user→permission map anywhere in the
  code or config.

```
                       ┌─────────────── AUTH_BACKEND ───────────────┐
  browser ── login ──▶ │  local  │   entra (SSO)  │   ldap          │
                       └────┬────────────┬────────────────┬─────────┘
                            │ groups      │ groups (claim) │ groups (dir)
                            ▼             ▼                ▼
                        session.user = { name, email, oid, groups: [...] }
                            │
                            ▼
        AUTHZ_GROUP_PERMISSIONS: { group -> [permissions] }   (groups only)
                            │
                            ▼
             require("manage_schedules")  ── 200 / 307 / 403
```

## Permissions

| Permission        | Guards                                                   |
|-------------------|---------------------------------------------------------|
| `manage_schedules` | Viewing/creating/editing/deleting schedules; viewing and **creating** IVRs; viewing/creating phrases |
| `manage_users`    | Admin: local user administration, the call-flow wizard (`/flow`), **deleting/recycling IVRs**, deleting phrases, plus all admin pages |
| `view_audit`      | Reading the tamper-evident **audit log** at `/admin/audit` (admin-only by default) — see [audit.md](audit.md) |

The **call-flow wizard** (`/flow`) wires an inbound route → time condition → IVR
in one step; because it can repoint inbound routes, it is **admin-only**
(`manage_users`). Building schedules and IVRs individually only needs
`manage_schedules` — but **deleting or recycling an IVR is admin-only**
(`manage_users`); regular schedule users can create IVRs, not remove them.

A logged-in user with **no** matching group gets **HTTP 403**. A user who is not
logged in is redirected (**307**) to `/auth/login`.

## Group → permission mapping

`AUTHZ_GROUP_PERMISSIONS` is a JSON object. The **key** is a group identifier
whose exact form depends on the backend:

| Backend | Group identifier used as the JSON key                        |
|---------|--------------------------------------------------------------|
| local   | the internal group name (e.g. `ivr-admins`)                  |
| entra   | the value in the token `groups` claim (object-id, or name if you configure name-based claims) |
| ldap    | the group `cn` (e.g. `ivr-admins`)                           |

```env
AUTHZ_GROUP_PERMISSIONS={"ivr-admins":["manage_schedules","manage_users"],"ivr-editors":["manage_schedules"]}
```

Backend-specific setup:

- **Local** — see below.
- **Entra ID SSO** — see [entra-sso.md](entra-sso.md).
- **LDAP** — see [ldap.md](ldap.md).
- **MFA & local admins (break-glass)** — see [mfa.md](mfa.md). Local admins are
  retained even under an external IdP and must complete MFA (passkey or TOTP).

## Local backend (default)

Users live in a SQLite DB (`LOCAL_AUTH_DB`, default `data/users.db`); passwords
are PBKDF2-HMAC-SHA256. Internal groups live in a `user_groups` table.

Seed an initial admin (only applied when the user table is empty). Use the
**`admin@local`** convention so the app's local admin is clearly distinct from
FusionPBX's own `admin` user:

```env
AUTH_BACKEND=local
LOCAL_ADMIN_USER=admin@local
LOCAL_ADMIN_PASSWORD=change-me
LOCAL_ADMIN_GROUP=ivr-admins        # seed admin is placed in this group
```

> **Upgrade note:** a local user previously seeded as **`admin`** is renamed to
> **`admin@local`** automatically the first time the user DB is opened (its
> groups, permissions, admin flag and MFA carry over). Sign in as `admin@local`
> afterwards. The rename is skipped if an `admin@local` user already exists.

Admins (`manage_users`) manage local users in the UI at **`/admin/users`**
(the **Users** nav link, shown only for the local backend): create/edit/delete
users, set the admin flag and groups, reset passwords, and reset MFA. The same is
available from the CLI:

```bash
python manage.py add alice --name "Alice A"     # prompts for password
python manage.py group-add alice ivr-editors    # grant perms via group
python manage.py groups alice
python manage.py passwd alice
python manage.py group-remove alice ivr-editors
python manage.py delete alice
python manage.py list
```

Because permissions come only from groups, a freshly-added user can log in but
can do nothing until placed in a group that appears in
`AUTHZ_GROUP_PERMISSIONS`.

**Local admins are superusers.** A local user with the `is_admin` flag (or in
`LOCAL_ADMIN_GROUP`) holds *every* permission regardless of `AUTHZ_GROUP_PERMISSIONS`
— so the seeded admin can manage the app out of the box. This is a role, not a
per-user permission binding; `is_admin` is only ever set by the local backend, so
IdP (Entra/LDAP/FusionPBX) users still derive their permissions purely from groups.

The seed admin's username follows the **`admin@local`** convention (distinct from
FusionPBX's own `admin`); a database previously seeded as `admin` is renamed to
`admin@local` once, carrying its groups, admin flag and MFA.

**Local admins work under any backend.** Even with `AUTH_BACKEND` set to an
external provider, local administrators authenticate against the local DB from the
**normal login form** (a local-admin fallback runs when the external lookup
doesn't match) as well as the break-glass page at `/auth/local`. Non-admin local
users get no such fallback.

## Self-service account (`/account`)

Every signed-in user has an account page (linked from their name in the top nav):

- **Local-DB accounts** (including a local admin signed in under an external
  backend) can change their **display name** and **password** (password change
  requires the current password).
- **External identities** (Entra / LDAP / FusionPBX) see a **read-only** table of
  name, email and groups, labelled with the source they came from.

## Interactive enablement & live testing (admin UI)

Users with `manage_users` get an **Auth config** page at **`/admin/auth`** to
enable and test a provider interactively before committing:

- **Entra**: enter tenant/client id/secret → **Test** fetches the tenant OIDC
  discovery doc and validates the client credentials by acquiring a token, and
  shows the redirect URI to register. You can also paste an ID token to inspect
  its `groups` claim and see which permissions it maps to.
- **LDAP**: enter connection + DN settings and a **test user/password** → **Test**
  connects, negotiates StartTLS, binds as that user, resolves their groups, and
  shows which map to permissions. Test credentials are used only for the probe
  and are never stored.
- **FusionPBX**: **Test** verifies a real username/password against `v_users` and
  shows the groups found and permissions they map to. A **group→permission mapping
  builder** loads the domain's groups and lets you tick permissions per group,
  writing `AUTHZ_GROUP_PERMISSIONS` for you. See [fpbx-auth.md](fpbx-auth.md).
- **Save** writes the validated values to an app-managed override file
  (`AUTH_CONFIG_FILE`, default `data/auth.env`) in the app's writable state dir —
  the root-owned main env file is never modified. **Restart the service** to
  apply.

Precedence: real process env vars > `AUTH_CONFIG_FILE` > `.env`. Manage a given
backend via the admin UI *or* via process env, not both.

## Session & cookie notes

- Sessions are signed cookies (`APP_SECRET_KEY`, `itsdangerous`). Set a long
  random secret in production; rotating it logs everyone out.
- Cookies are issued `Secure` + `SameSite=Lax`. Terminate TLS at your reverse
  proxy and serve the app over HTTPS, or browsers will not send the cookie back.
