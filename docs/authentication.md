# Authentication & Authorization

The app separates **authentication** (who are you?) from **authorization**
(what may you do?).

- **Authentication** is handled by a pluggable backend selected with
  `AUTH_BACKEND`: `local` (default), `entra` (Microsoft Entra ID SSO), or
  `ldap`. Each backend produces a session identity that includes the user's
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
| `manage_schedules` | Viewing, creating, editing, deleting schedules (all UI)  |
| `manage_users`    | Reserved for local user administration                  |

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

Seed an initial admin (only applied when the user table is empty):

```env
AUTH_BACKEND=local
LOCAL_ADMIN_USER=admin
LOCAL_ADMIN_PASSWORD=change-me
LOCAL_ADMIN_GROUP=ivr-admins        # seed admin is placed in this group
```

Manage users and group membership with the CLI:

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
IdP (Entra/LDAP) users still derive their permissions purely from groups.

## Interactive enablement & live testing (admin UI)

Users with `manage_users` get an **Auth config** page at **`/admin/auth`** to
enable and test SSO/LDAP interactively before committing:

- **Entra**: enter tenant/client id/secret → **Test** fetches the tenant OIDC
  discovery doc and validates the client credentials by acquiring a token, and
  shows the redirect URI to register. You can also paste an ID token to inspect
  its `groups` claim and see which permissions it maps to.
- **LDAP**: enter connection + DN settings and a **test user/password** → **Test**
  connects, negotiates StartTLS, binds as that user, resolves their groups, and
  shows which map to permissions. Test credentials are used only for the probe
  and are never stored.
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
