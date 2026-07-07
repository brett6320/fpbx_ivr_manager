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
             require("manage_closures")  ── 200 / 307 / 403
```

## Permissions

| Permission        | Guards                                                   |
|-------------------|---------------------------------------------------------|
| `manage_closures` | Viewing, creating, editing, deleting closures (all UI)  |
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
AUTHZ_GROUP_PERMISSIONS={"ivr-admins":["manage_closures","manage_users"],"ivr-editors":["manage_closures"]}
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

## Session & cookie notes

- Sessions are signed cookies (`APP_SECRET_KEY`, `itsdangerous`). Set a long
  random secret in production; rotating it logs everyone out.
- Cookies are issued `Secure` + `SameSite=Lax`. Terminate TLS at your reverse
  proxy and serve the app over HTTPS, or browsers will not send the cookie back.
