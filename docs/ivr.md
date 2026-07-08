# IVR menus

The app builds native FusionPBX **IVR menus**: a greeting, per-digit caller
options that route to real FusionPBX destinations, and a timeout destination.
They slot into the call flow as:

```
Destination (inbound route) → Time Condition (schedule) → IVR → dialed option / timeout
```

Manage them at **`/ivrs`** (requires `manage_schedules`).

## What gets created

Per IVR, on a managed extension (9550–9599):

- a **`v_ivr_menus`** row (greeting, timeout, exit action) + **`v_ivr_menu_options`**
  rows (one per digit), and a **dialplan** stamped with the FusionPBX IVR-menus
  `app_uuid` so it appears natively in the FusionPBX IVR Menus GUI;
- the dialplan answers, sets `ivr_menu_uuid`, and calls FusionPBX's
  `ivr_menu.lua` (which reads the menu/options from the DB live), then runs the
  timeout/exit action.

Ownership is tracked by our marker (in `ivr_menu_description` and the dialplan
XML) — the app never edits or deletes an IVR it didn't create.

## Greeting

Two choices:

- **Google TTS** — type the menu script; it's synthesized and stored as a
  recording (same path as schedule greetings).
- **Existing recording** — pick any recording already in the domain
  (`v_recordings`).

## Destinations (options + timeout)

The option and timeout dropdowns are built from the domain's real destinations,
mirroring what FusionPBX offers:

| Kind | Source | Target |
|------|--------|--------|
| Extension | `v_extensions` | `transfer <ext> XML <domain>` |
| Ring group | `v_ring_groups` | `transfer <rg_ext> XML <domain>` |
| Voicemail box | `v_voicemails` | `transfer *99<id> XML <domain>` |

So "press 0 → after-hours ring group" is just picking that ring group for digit
`0`. The **timeout destination** (usually a voicemail box or answering service)
can be picked from the list **or** entered as an external/manual number for an
off-box answering service.

## Wiring into the flow

To send callers into an IVR, set a **schedule's open destination** to the IVR's
extension — that's the Time Condition → IVR link. Point your inbound route at the
schedule's extension to complete `inbound → time condition → IVR → option/timeout`.

> A one-flow wizard that also creates the inbound route automatically is the next
> increment; today the IVR + schedule pieces are built here and the inbound route
> is pointed at the schedule in FusionPBX (or by editing the schedule's open
> destination to the IVR extension).

## Caveat

As with schedules, manage IVRs **through this app**. The dialplan is written
directly; editing the IVR's dialplan in the FusionPBX GUI can strip the ownership
marker, after which the app treats it as foreign (fail-safe). Untested against a
live box — verify on your instance (see [compatibility.md](compatibility.md)).
