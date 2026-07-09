# Export & import

Admins (`manage_users`) can export the app-managed items and import them into
another instance from the **Backup** page (`/admin/import`).

## Export

`GET /admin/export` downloads a JSON document (current `version` = 2) with:

- **business** — business name + templates
- **schedules** — each managed **time condition** (extension, name, open
  destination) with its list of **closures** (label, window, reason, closed
  action) — a time condition can hold several closures
- **ivrs** — each managed IVR (extension, name, greeting recording, timeout
  destination, and options)

Older `version: 1` documents (a single flat closure per schedule) still import.

## Import — reviewed item by item

**Nothing is imported automatically.** Uploading an export file takes you to a
**review page** that lists every item individually:

1. Each item has an **include** checkbox (checked by default) and an **editable
   JSON** field — tweak anything before committing.
2. **Commit selected** applies only the checked items, one at a time, and reports
   a per-item result (a failure on one item does not stop the others).

Item types: `business`, `schedule`, `ivr`.

## Notes

- Importing a **schedule** (time condition) or **IVR** re-generates greeting audio
  via Google TTS on the target — so the target needs `GOOGLE_TTS_CREDENTIALS_FILE`
  set. Each closure's *reason* round-trips (it's stored in the dialplan and
  re-exported), so greetings regenerate faithfully; you can still edit it on the
  review page. An IVR exports its greeting **recording** filename; to re-synthesize
  instead, clear `greeting_recording` and set `greeting_text` on the review page.
- Guardrails still apply on commit: the app refuses to overwrite records it didn't
  create, and new schedules/IVRs must land in the managed extension pool.
