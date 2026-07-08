# MFA & local administrators

Local administrator accounts are **retained and usable even when the primary
`AUTH_BACKEND` is an external IdP** (Entra or LDAP), and they are **required to
complete multi-factor authentication** — a passkey (WebAuthn) or a TOTP
authenticator app.

## Who is a "local admin"?

A local user is a local admin if **either**:

- their `is_admin` flag is set (`manage.py add --admin`, or `manage.py set-admin`), **or**
- they belong to the group named by `LOCAL_ADMIN_GROUP` (default `ivr-admins`).

> Keep `LOCAL_ADMIN_GROUP` distinct from your permission groups if you don't want
> permission-group membership to also imply "local admin + MFA required".

MFA enforcement here applies specifically to **local** admins. External IdP users
satisfy MFA at the IdP; the `is_admin` flag is only ever set by the local backend.

## Break-glass sign-in

Regardless of `AUTH_BACKEND`, a local-admin login form is always available at:

```
/auth/local
```

When the primary backend is external, the standard login page shows a
**"Local administrator sign-in (break-glass)"** link to it. This path:

- authenticates against the **local** user store only,
- is **restricted to local administrators** (a valid non-admin local user is
  refused), and
- enforces MFA exactly like the local backend does.

This guarantees you can always get in to manage schedules if the IdP is
unavailable or misconfigured — provided you keep a local admin with an enrolled
factor.

## Login flow

```
password OK ──► local admin? ──no──► logged in
                     │yes
                     ▼
             MFA factor enrolled? ──no──► /auth/mfa/setup  (enrol passkey or TOTP)
                     │yes                        │ on success
                     ▼                           ▼
              /auth/mfa (verify) ─────────► logged in
```

A partially-authenticated admin (password accepted, MFA not yet completed) holds
**no** usable session — protected pages redirect back to login until MFA passes.

## Factors

### Passkey (WebAuthn) — recommended

- Requires the extra: `pip install '.[passkey]'`.
- Relying-party id and origin are derived from `APP_BASE_URL`
  (`webauthn_rp_id` = host, `webauthn_origin` = scheme+host). **Passkeys are
  bound to that origin** — serve the app over HTTPS on a stable hostname.
- Enrolment and verification happen in the browser via the WebAuthn API; the
  server issues/verifies challenges (`app/mfa/passkey.py`) and stores the
  credential id + public key in the `webauthn_credentials` table.

### TOTP (authenticator app)

- No extra dependencies (stdlib, `app/mfa/totp.py`): RFC 6238, SHA-1, 6 digits,
  30-second period, ±1 step drift tolerance.
- On setup the app shows the base32 secret and an `otpauth://` URI to scan into
  Google Authenticator, Authy, 1Password, etc.

## Administration (CLI)

```bash
python manage.py add alice --admin        # create a local admin (MFA required)
python manage.py set-admin alice          # promote existing user
python manage.py set-admin alice --off    # demote
python manage.py mfa-reset alice          # clear factors; alice must re-enrol next login
```

The seed admin (`LOCAL_ADMIN_USER`/`LOCAL_ADMIN_PASSWORD`) is created with the
admin flag and placed in `LOCAL_ADMIN_GROUP` on first startup, and must enrol MFA
at first login.

## Dev mode

Setting `DEV_MODE=true` **disables MFA enforcement for local admins** (no forced
enrollment or verification) for local development. It logs a startup warning and
must **never** be enabled in production.

## Recovery

If an admin loses their factor, another admin runs `manage.py mfa-reset <user>`
(or an operator with shell access does). On the next login the user is sent back
through enrolment. Keep **at least two** local admins with enrolled factors so a
single lost device never locks you out.
