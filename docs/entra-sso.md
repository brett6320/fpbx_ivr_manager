# Microsoft Entra ID (Azure AD) SSO

Enables single sign-on via the OpenID Connect **authorization-code flow** (MSAL
confidential client). Users are redirected to Microsoft to sign in; the app
never sees their password. Group membership from the token drives authorization.

- Backend: `AUTH_BACKEND=entra`
- Library: `msal` (already a core dependency)
- Login route: `GET /auth/login` → 302 to Microsoft
- Callback route: `GET /auth/callback` (must match the registered redirect URI)

## 1. Register the application

In the [Entra admin center](https://entra.microsoft.com) → **Identity →
Applications → App registrations → New registration**:

1. **Name**: e.g. `FusionPBX IVR Manager`.
2. **Supported account types**: *Accounts in this organizational directory only*
   (single tenant) unless you specifically need multi-tenant.
3. **Redirect URI**: platform **Web**, value:

   ```
   https://ivr.example.com/auth/callback
   ```

   This must exactly equal `{APP_BASE_URL}/auth/callback`. Register one per
   environment (prod, staging, `https://localhost:8080/auth/callback` for local).
4. Click **Register**. Copy the **Application (client) ID** and **Directory
   (tenant) ID** from the Overview page.

## 2. Create a client secret

**Certificates & secrets → Client secrets → New client secret**. Copy the
secret **Value** (not the Secret ID) immediately — it is shown only once. Note
the expiry and set a rotation reminder.

## 3. Emit a groups claim

Authorization needs the user's groups in the ID token.

**Token configuration → Add groups claim**:

- Select the group types to emit (**Security groups** is typical).
- Under **ID**, choose the identifier emitted in the claim:
  - **Group ID** (default): the claim contains group **object IDs** (GUIDs).
    Use those GUIDs as the keys in `AUTHZ_GROUP_PERMISSIONS`.
  - **sAMAccountName / group names** (requires groups synced from AD and the
    "Emit groups as role claims"/name option): the claim contains readable
    names — easier to map, but only available for AD-synced groups.

> **Overage:** if a user is a member of **more than ~200 groups**, Entra omits
> the `groups` claim and instead returns a link to Microsoft Graph. This app
> reads the claim directly and does **not** call Graph, so keep the assigned
> groups small, or restrict emitted groups to *"Groups assigned to the
> application"* (see step 4) to stay under the limit.

## 4. (Recommended) Restrict and scope access

- **Enterprise applications → your app → Properties → Assignment required?** =
  **Yes**, then **Users and groups → Add** the specific security groups that may
  use the tool. Combined with **Token configuration → groups claim → Groups
  assigned to the application**, the token then carries only the relevant
  groups.
- API permissions: the default delegated **User.Read** (OpenID `openid profile`)
  is sufficient. No admin consent beyond sign-in is required.

## 5. Configure the app

```env
AUTH_BACKEND=entra
APP_BASE_URL=https://ivr.example.com

ENTRA_TENANT_ID=<Directory (tenant) ID>
ENTRA_CLIENT_ID=<Application (client) ID>
ENTRA_CLIENT_SECRET=<client secret VALUE>

# Map the group identifiers that appear in the token 'groups' claim.
# Using Group IDs (GUIDs):
AUTHZ_GROUP_PERMISSIONS={"11111111-1111-1111-1111-111111111111":["manage_schedules","manage_users"],"22222222-2222-2222-2222-222222222222":["manage_schedules"]}
```

At startup the app validates that `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, and
`ENTRA_CLIENT_SECRET` are set, and fails fast otherwise.

## 6. Verify

1. Browse to `https://ivr.example.com/` → you are redirected to Microsoft.
2. Sign in with a user who is a member of a mapped group → you land on the
   schedules list.
3. Sign in as a user in **no** mapped group → **403** (authenticated but
   unauthorized), proving group binding works.

## How it maps internally

`app/auth/entra.py`:

- `auth_url(state)` builds the authorize URL with the registered redirect URI.
- `redeem_code(code)` exchanges the code for tokens (MSAL
  `acquire_token_by_authorization_code`).
- `user_from_claims(claims)` extracts `name`, `email`
  (`preferred_username`), `oid`, and **`groups`** (the `groups` claim, coerced
  to a list). The groups flow straight into `AUTHZ_GROUP_PERMISSIONS` resolution.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `AADSTS50011` redirect mismatch | Registered redirect URI ≠ `{APP_BASE_URL}/auth/callback`. Match scheme/host/path exactly. |
| Logged in but always 403 | Groups claim missing or keys don't match. Decode the ID token (jwt.ms) and confirm the `groups` values equal your `AUTHZ_GROUP_PERMISSIONS` keys. |
| No `groups` in token | Groups claim not configured (step 3), or group-count overage (step 3 note). |
| `invalid_client` | Wrong/expired client secret, or Secret **ID** used instead of the **Value**. |
| Redirect loop | Cookie not returned — serve over HTTPS (cookies are `Secure`). |

## Interactive testing

Prefer the admin UI at **`/admin/auth`** (requires `manage_users`) to enter these
values and **Test** them live — it validates the tenant discovery doc and your
client credentials, shows the redirect URI to register, and can decode an ID
token to confirm the `groups` claim maps to permissions. See
[authentication.md](authentication.md#interactive-enablement--live-testing-admin-ui).
