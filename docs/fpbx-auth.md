# FusionPBX user authentication

`AUTH_BACKEND=fpbx` authenticates sign-ins against the **FusionPBX user database**
(`v_users`) for the configured domain, reusing the app's existing PostgreSQL
connection — no separate directory or IdP to run. A user's FusionPBX **groups**
become their `groups`, mapped to app permissions through
`AUTHZ_GROUP_PERMISSIONS`, exactly like the Entra and LDAP backends.

```env
AUTH_BACKEND=fpbx
AUTHZ_GROUP_PERMISSIONS={"superadmin":["manage_schedules","manage_users"],"agents":["manage_schedules"]}
```

The keys are FusionPBX **group names** (`v_group_users.group_name`, e.g.
`superadmin`, `agents`). A FusionPBX user with no mapped group can sign in but has
no permissions until a group they belong to appears in the map.

## How a login is verified

1. Look up the enabled user (`user_enabled = 'true'`) by `username` in the
   configured domain — falling back to a **global** (domain-less) FusionPBX
   account if there's no domain-specific match.
2. Verify the password against FusionPBX's own hashing, covering 4.5.x → 5.5:
   - **bcrypt** — stored `$2y$…` (also `$2a$`/`$2b$`). The PHP `$2y$` prefix is
     rewritten to `$2b$` for verification. Needs the `bcrypt` package
     (`pip install '.[fpbx]'`).
   - **legacy MD5** — `md5(salt + password)`, then `md5(password)`. Weak, but
     matches FusionPBX's own legacy fallback so older accounts still work.
   Other crypt schemes (`$6$` sha512, argon2, …) are **not** accepted.
3. Resolve the user's groups from `v_group_users` and map them to permissions.

The session identity's email is the FusionPBX **username** — FusionPBX stores a
user's email in `v_contacts` (via `contact_uuid`), not `v_users`, and some schema
versions have no email column on `v_users` at all, so it isn't read.

Permissions remain **group-only**; this backend never sets the local `is_admin`
superuser flag (that's exclusive to the local backend).

## Database privileges

The app must be able to read three tables. With the scoped role
(`sql/least_privilege_role.sql`):

```sql
GRANT SELECT ON v_users       TO ivr_manager;
GRANT SELECT ON v_group_users TO ivr_manager;
GRANT SELECT ON v_groups      TO ivr_manager;
```

> **Note:** `v_users` holds password hashes. Granting `SELECT` lets the app read
> them to verify logins — expected for this backend, but omit these grants if you
> don't use it.

## Test before committing

On **`/admin/auth`**, pick *FusionPBX users* and use **Test FusionPBX login &
groups**: it verifies a real username/password against the database and shows the
groups found and the permissions they map to. Test credentials are used only for
the probe and are never stored. Then **Save** and restart the service to apply.

## MFA

MFA (passkey/TOTP) is enforced by the app independently of the backend, so it
still applies to FusionPBX-backed logins — see [mfa.md](mfa.md).
