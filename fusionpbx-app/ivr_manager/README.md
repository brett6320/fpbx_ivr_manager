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

```bash
# 1. copy the app into your FusionPBX app directory
sudo cp -r fusionpbx-app/ivr_manager /var/www/fusionpbx/app/ivr_manager
sudo chown -R www-data:www-data /var/www/fusionpbx/app/ivr_manager   # or the web user on your distro

# 2. register it: FusionPBX GUI → Advanced → Upgrade → tick
#    "App Defaults", "Permission Defaults", "Menu Defaults" → Execute
#    (or CLI: sudo php /var/www/fusionpbx/core/upgrade/upgrade.php)
```

Then sign out/in and open **IVR Manager** from the menu (relocate it via
**Advanced → Menu Manager** if you like). Permissions created:
`ivr_manager_schedule_view` / `_add` / `_edit` / `_delete` (delete is
superadmin/admin only by default).

## What it does today

- **Time conditions** on the managed extension pool (default `9550–9599`, set under
  **Advanced → Default Settings → category `ivr_manager`**). One condition holds any
  number of **closures**, each with its own window, *when-closed* action
  (voicemail/hangup), reason and greeting recording. Closures are emitted
  shortest-window-first so a narrow closure shadows a broader overlap; a trailing
  condition routes to the **open (daytime) destination** when none match.
- Greeting per closure is picked from existing **recordings** (`v_recordings`).
- Reloads the dialplan via `event_socket` after a change (falls back silently — you
  can Reload XML from the GUI).

**Not yet ported** (on the Python app, planned here): Google-TTS greeting
generation, IVR-menu builder, and the one-flow wizard. Contributions welcome.

## Caveat

Like the Python app, **manage these through the app** — editing a managed time
condition in the FusionPBX Time Conditions GUI can strip the ownership marker,
after which the app treats it as foreign (fail-safe). Untested against every live
FusionPBX build; verify on your instance.
