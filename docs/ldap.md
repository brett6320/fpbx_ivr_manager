# LDAP authentication

Authenticates users against an LDAP / Active Directory server by **binding as the
user** with the supplied password (a successful bind = valid credentials). Group
memberships read from the directory drive authorization.

- Backend: `AUTH_BACKEND=ldap`
- Library: `ldap3` — install the extra: `pip install '.[ldap]'`
- Login: form-based (`GET`/`POST /auth/login`), same UI as the local backend.

## How it works

`app/auth/ldap_backend.py`:

1. Renders a user DN from `LDAP_BIND_DN_TEMPLATE` (e.g.
   `uid=alice,ou=people,dc=example,dc=com`).
2. Opens a connection to `LDAP_URI` and **binds as that DN** with the password.
   If `LDAP_START_TLS=true`, StartTLS is negotiated before the bind so the
   password never crosses the wire in clear text. A failed bind → login denied.
   An empty password is rejected outright (no anonymous bind).
3. Resolves the display name and **group memberships**:
   - First from the user entry's `memberOf` attribute (AD and many servers).
   - If `memberOf` is empty, it searches `LDAP_GROUP_BASE_DN` with
     `LDAP_GROUP_FILTER` (default `(member={user_dn})`) and collects each group
     `cn` (works for OpenLDAP `groupOfNames`, posixGroups, etc.).
4. Returns `{ name, email, oid: <user dn>, groups: [<cn>, ...] }`.

Group **`cn`** values are the identifiers you map in `AUTHZ_GROUP_PERMISSIONS`.

## Configuration

```env
AUTH_BACKEND=ldap

# Connection — use ldaps:// (636) or ldap:// with StartTLS.
LDAP_URI=ldaps://ldap.example.com
LDAP_START_TLS=true

# User bind: {username} is substituted from the login form.
LDAP_BIND_DN_TEMPLATE=uid={username},ou=people,dc=example,dc=com

# Where/how to look up the user entry (display name, memberOf).
LDAP_BASE_DN=ou=people,dc=example,dc=com
LDAP_USER_FILTER=(uid={username})

# Group lookup fallback (when memberOf is absent). {user_dn} and {username} available.
LDAP_GROUP_BASE_DN=ou=groups,dc=example,dc=com
LDAP_GROUP_FILTER=(member={user_dn})

# Grant permissions by group cn:
AUTHZ_GROUP_PERMISSIONS={"ivr-admins":["manage_schedules","manage_users"],"ivr-editors":["manage_schedules"]}
```

The app validates at startup that `LDAP_URI` and `LDAP_BIND_DN_TEMPLATE` are set.

### Active Directory specifics

AD users typically bind with `userPrincipalName` or `DOMAIN\sAMAccountName`
rather than a DN template:

```env
# Bind with UPN — {username} is the full user@domain the person types:
LDAP_BIND_DN_TEMPLATE={username}
LDAP_BASE_DN=DC=example,DC=com
LDAP_USER_FILTER=(userPrincipalName={username})
# AD populates memberOf, so LDAP_GROUP_* fallback is usually unnecessary.
```

AD `memberOf` yields full group DNs; the app takes the leading `cn=` component,
so a group `CN=ivr-admins,OU=Groups,DC=example,DC=com` maps under the key
`ivr-admins`.

### OpenLDAP with groupOfNames

`groupOfNames` entries reference members by DN and users usually lack
`memberOf` (unless the `memberof` overlay is enabled). Rely on the group-search
fallback:

```env
LDAP_GROUP_BASE_DN=ou=groups,dc=example,dc=com
LDAP_GROUP_FILTER=(&(objectClass=groupOfNames)(member={user_dn}))
```

## Security notes

- **Always** use `ldaps://` or `LDAP_START_TLS=true`. Without TLS the bind
  password is sent in clear text.
- The app performs a **direct user bind** — it does not need, and you should not
  configure, a privileged service-account password for authentication itself.
- Empty passwords are rejected so a misconfigured directory can't grant an
  anonymous-bind "success".

## Verify

1. `pip install '.[ldap]'`, set the env, start the app.
2. Log in as a directory user in a mapped group → schedules list.
3. Log in as a valid user in **no** mapped group → **403** (auth OK, not
   authorized).
4. Wrong password → form re-displays with "Invalid username or password".

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| All logins fail | `LDAP_BIND_DN_TEMPLATE` wrong, or TLS handshake failing. Test with `ldapwhoami -H "$LDAP_URI" -D "<dn>" -W`. |
| Login OK but 403 | Groups not resolved or `cn` keys don't match. Check the user's `memberOf`, or that the group search base/filter returns entries. |
| No groups found | `memberOf` not populated **and** `LDAP_GROUP_BASE_DN`/filter unset or wrong. Set the fallback. |
| `ldap3` ImportError | Install the extra: `pip install '.[ldap]'`. |
| Cleartext concern | Set `LDAP_START_TLS=true` or switch `LDAP_URI` to `ldaps://`. |

## Interactive testing

Prefer the admin UI at **`/admin/auth`** (requires `manage_users`) to enter these
values and **Test** them live — it connects, negotiates StartTLS, binds as a test
user you supply, and shows the resolved groups and their permission mapping
before you save. See
[authentication.md](authentication.md#interactive-enablement--live-testing-admin-ui).
