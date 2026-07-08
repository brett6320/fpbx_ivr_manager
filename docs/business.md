# Business profile & prompt placeholders

Admins (`manage_users`) set a **business name** and reusable **business-hours
templates** at **`/admin/business`**. These become **placeholders** that can be
dropped into any prompt (schedule reason, IVR greeting, call-flow greeting) and
are plugged in when the audio is generated.

## Placeholders

| Placeholder | Value |
|---|---|
| `{business_name}` | The configured business name (falls back to `APP_ORG_NAME` if blank). |
| `{hours.<Template>}` | The text of the named business-hours template, e.g. `{hours.Standard}`. |
| `{business_hours}` | The first template's text (a convenient default). |

Unknown placeholders are left untouched.

### Example

Business name `Acme Co`, template **Standard** = `Monday to Friday, 9 AM to 5 PM`.

IVR greeting:

> `Thank you for calling {business_name}. Our normal hours are {hours.Standard}. For sales press 1, press 0 for the operator.`

is synthesized as:

> Thank you for calling Acme Co. Our normal hours are Monday to Friday, 9 AM to 5 PM. For sales press 1, press 0 for the operator.

## Where it applies

Substitution runs on the free-text you author before Text-to-Speech:

- **IVR greeting** (`/ivrs/new`, `/flow/new`)
- **Schedule reason** (`/schedules/new`) — the closed greeting also uses the
  business name as the "Thank you for calling …" opener.

The greeting/reason forms list the available placeholders as a hint.

## Storage

The profile is an app-managed JSON file in the writable state dir
(`BUSINESS_CONFIG_FILE`, default `data/business.json`), written `0600` — no
FusionPBX changes. Editing it is admin-only.
