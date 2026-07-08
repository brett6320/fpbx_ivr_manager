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

Two choices — **using an existing phrase is the default** (when any recordings
exist), so you reuse a pre-recorded prompt rather than generating a new one:

- **Existing phrase** (default) — pick any recording already in the domain
  (`v_recordings`).
- **Google TTS** — type the menu script; it's synthesized and stored as a
  recording.

Phrases this app generates via TTS are named with the **`ivrmgr_`** prefix
(e.g. `ivrmgr_ivr_9560_main_menu`, `ivrmgr_schedule_9550_holiday`) so they're
easy to identify and filter in the phrase picker and in FusionPBX recordings.

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

Two ways:

- **One-flow wizard (`/flow/new`)** — build the whole chain in one step: it
  creates the IVR, a time condition (schedule) whose open destination routes into
  the IVR, and — if you pick an inbound DID — points that **inbound route**
  (Destinations app, `public` context) at the schedule. Result:
  `inbound → time condition → IVR → option/timeout`.
- **Piece by piece** — build an IVR here, then set a **schedule's open
  destination** to the IVR's extension, and point your inbound route at the
  schedule's extension yourself.

The inbound DID is a **selector of the domain's existing inbound routes** (each
labelled *managed* or *existing route*), so you repurpose a real destination
rather than typing a number. **Safeguard:** if the selected DID's route was not
created by this app, the wizard **refuses to overwrite it** — and does not create
anything — unless you tick *"replace the existing inbound route."* Managed routes
are updated freely.

## Caveat

As with schedules, manage IVRs **through this app**. The dialplan is written
directly; editing the IVR's dialplan in the FusionPBX GUI can strip the ownership
marker, after which the app treats it as foreign (fail-safe). Untested against a
live box — verify on your instance (see [compatibility.md](compatibility.md)).
