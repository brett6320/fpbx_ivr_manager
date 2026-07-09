# IVR Manager — FusionPBX PHP app (native variant)

A **native FusionPBX app** that manages planned office-closure **time conditions**
(with **multiple closures per condition**, most-specific-first) directly inside the
FusionPBX GUI. It reuses FusionPBX's own **authentication, session, permissions and
database** — so there's no separate service, no extra runtime, and no Python.

This is the alternate to the standalone Python app (root of this repo). Pick one:

| | Python app (repo root) | This FusionPBX PHP app |
|---|---|---|
| Runs as | its own service (Docker/systemd) | inside FusionPBX (Apache/PHP) |
| Auth | local / Entra / LDAP / FusionPBX | **FusionPBX's** (groups/permissions) |
| Runtime | Python 3.11+ | the host's **PHP** (no new runtime) |
| Best when | you want SSO/MFA, or run off-box | you want it native on the PBX host |

## Compatibility

- **FusionPBX 4.x → 5.x.** Uses the stable app framework (permissions, menu,
  default settings) and writes only to columns that exist on the running schema
  (`resources/classes/ivr_schedule.php` → `save()`), so schema drift across versions
  is tolerated.
- **PHP 7.0** (the FusionPBX 4.x floor) through the **current GA (8.5)** — no
  version-specific syntax. CI (`.github/workflows/php-lint.yml`) `php -l`s every
  file on 7.0, 7.3, 8.3, 8.4 and 8.5.
- Records are stamped with FusionPBX's Time Conditions `app_uuid`
  (`4b821450-…`), so they appear as **native Time Conditions** in the GUI, and with
  an **ownership marker** comment so the app never edits/deletes a dialplan it
  didn't create.

## Install

The easiest way is the bundled installer — it auto-detects the FusionPBX
directory and web user, copies the app, and registers it:

```bash
sudo ./fusionpbx-app/install.sh
# non-interactive:
# sudo FUSIONPBX_DIR=/var/www/fusionpbx RUN_UPGRADE=yes ASSUME_YES=1 ./fusionpbx-app/install.sh
```

Or do it by hand:

```bash
# 1. copy the app into your FusionPBX app directory
sudo cp -r fusionpbx-app/ivr_manager /var/www/fusionpbx/app/ivr_manager
sudo chown -R www-data:www-data /var/www/fusionpbx/app/ivr_manager   # or the web user on your distro

# 2. register it: FusionPBX GUI → Advanced → Upgrade → tick
#    "App Defaults", "Permission Defaults", "Menu Defaults" → Execute
#    (or CLI: sudo -u www-data php /var/www/fusionpbx/core/upgrade/upgrade.php)
```

Then sign out/in and open **IVR Manager** from the menu (relocate it via
**Advanced → Menu Manager** if you like). Permissions created:
`ivr_manager_schedule_view` / `_add` / `_edit` / `_delete` (delete is
superadmin/admin only by default).

> Tested target: **FusionPBX 4.5.1 on PHP 7.3** (the low end of support). It uses
> only stable framework calls and shims the parts that differ on older builds
> (see below), so it should also run unchanged on 5.x.

## FusionPBX integration — how it hooks in

Every integration point reuses FusionPBX rather than reinventing it:

| Concern | How | Notes |
|---|---|---|
| **Auth / session** | `resources/check_auth.php` + `$_SESSION` | no separate login; the FusionPBX session is authoritative |
| **Authorization** | `permission_exists('ivr_manager_schedule_*')` | permissions created from `app_config.php`; bound to groups |
| **Database** | the FusionPBX `database` object's PDO (`$database->db`) | **no schema changes** — writes go to the existing `v_dialplans` |
| **Native Time Conditions** | rows stamped with the TC `app_uuid` (`4b821450-…`) | appear in the FusionPBX **Time Conditions** GUI |
| **Ownership guardrail** | marker comment in `dialplan_xml` | refuses to edit/delete a dialplan it didn't create |
| **Schema drift (4.x↔5.x)** | `ivr_schedule::save()` inserts only columns that exist | tolerant of column differences across versions |
| **Recordings** | greeting picked from `v_recordings`; playback uses `$${recordings}/<domain>/<file>` | no host-path/session-key dependency |
| **Google TTS** | RS256 JWT via `openssl_sign` → token → REST synth; stores a `v_recordings` row | no Composer/library; optional |
| **Secrets** | service-account JSON in the app's own `v_ivr_manager_settings` table | **write-only** in the UI — never rendered back |
| **Apply changes** | `event_socket` → `api reloadxml` (best-effort) | falls back silently; Reload XML from the GUI works too |
| **UI chrome** | `resources/header.php` / `footer.php`, `$document['title']` | looks native |
| **Version shims** | `resources/functions.php` | `button`/`message`/`escape` differ across 4.5.x↔5.x — wrappers feature-detect and fall back to plain HTML/stdlib |

No cron, no daemon, no extra ports, no Composer packages. Uninstall = delete
`app/ivr_manager/` and remove its menu/permissions in the GUI; the time conditions
it created remain as normal FusionPBX dialplans.

## Troubleshooting

- **App/menu doesn't appear** → run *Advanced → Upgrade → App/Permission/Menu
  Defaults*, then sign out/in.
- **"access denied"** → your group lacks `ivr_manager_schedule_view`; grant it in
  *Advanced → Group Manager* (or re-run Permission Defaults).
- **Class not found (`ivr_schedule`)** → the pages `require_once` it explicitly, so
  this shouldn't happen; ensure the whole folder copied (including
  `resources/classes/`).
- **Greeting doesn't play** → confirm the recording exists in *Apps → Recordings*
  for the domain; the dialplan references `$${recordings}/<domain>/<file>`.
- **Change not live** → click *Advanced → Reload XML* (the automatic reload is
  best-effort via `event_socket`).

## What it does today

- **Time conditions** on the managed extension pool (default `9550–9599`, set under
  **Advanced → Default Settings → category `ivr_manager`**). One condition holds any
  number of **closures**, each with its own window, *when-closed* action
  (voicemail/hangup), reason and greeting recording. Closures are emitted
  shortest-window-first so a narrow closure shadows a broader overlap; a trailing
  condition routes to the **open (daytime) destination** when none match.
- The **open (fall-through) destination** is a picker of the domain's real
  destinations — including **other time conditions** — so a closure TC can fall
  through to your office-hours TC (inbound → closure TC → office-hours TC).
- **Business** page: default destinations for **on-hours / off-hours / emergency**;
  the on-hours default pre-fills a new time condition's fall-through.
- Greeting per closure is either an existing **recording** (`v_recordings`) **or**
  generated from text via **Google Cloud TTS** (LINEAR16 @ 8 kHz), stored as a
  recording named `ivrmgr_…`.
- Reloads the dialplan via `event_socket` after a change (falls back silently — you
  can Reload XML from the GUI).

### Google TTS (write-only credentials)

Under **TTS settings** (button on the list page; permission
`ivr_manager_tts_manage`, admin) you paste the Google **service-account JSON** and
set the voice/language. The credential is **write-only**: it's stored (in the
app's own `v_ivr_manager_settings` table, auto-created — never in the browsable
Default Settings UI) and **never rendered back**. A **Test** button verifies
validity (mints a token and does a one-word synth, reporting the authenticated
account) without revealing the key. Auth uses an RS256 JWT signed with
`openssl_sign` — no Composer/library needed, works on PHP 7.x+.

**Not yet ported** (on the Python app, planned here): IVR-menu builder and the
one-flow wizard. Contributions welcome.

## Caveat

Like the Python app, **manage these through the app** — editing a managed time
condition in the FusionPBX Time Conditions GUI can strip the ownership marker,
after which the app treats it as foreign (fail-safe). Untested against every live
FusionPBX build; verify on your instance.
