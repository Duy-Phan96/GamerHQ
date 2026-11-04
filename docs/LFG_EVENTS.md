# LFG events and active lobbies

## Code ownership

When the independently installed Event Manager is available, `cogs/event_manager.py`
routes to `cogs/event_editor.py`. This Discord adapter reuses the compact V1
builder controls from `cogs/lfg.py`, but never calls their V1 persistence, recurrence
engine or resource creation handlers. Validation, recurrence, participants,
persistence and lifecycle remain exclusively in the installed Skill's public APIs.

Creation and editing share date, time, player limit and One-time / Recurring
dropdowns; Title (including game and description), AM/PM, voice reminder,
public/private and Preview buttons. Recurring opens seven independent weekday
buttons. Preview opens Edit / Create Event (or Save changes) / Cancel, plus player
pickers and event settings. Every persistent save requires a fresh validated
preview; editing retains the original revision and creation retains one request ID.
Advanced settings retain exact wall times, timezones, capacity, duration,
auto-enrollment, chat, voice and explicit imported-schedule conversion. User pickers
add/remove event members and invitations without dropping existing selections;
single-occurrence participation remains under Join/Leave and `/lfg participants`.
New drafts retain the established Host timezone, and existing event timezones are
preserved. DST interpretation and rolling indefinite recurrence remain Skill-owned.
The component snapshot is `tests/fixtures/skill-event-compact-ui.json`; live Discord
acceptance is separately required. The sections below describe the retained V1 flow.

- cogs/lfg.py: creation/preview/join/share UI, Discord role assignment, private text/voice, notifications, persistent views and scheduler.
- services/lfg_service.py: game eligibility, lookups, rendering and Europe/Berlin date parsing; ambiguous/nonexistent DST times are rejected.
- services/lobby_service.py: transactional host/member rules. BEGIN IMMEDIATE serializes final capacity/state checks.
- services/lobby_dashboard.py: event locks, canonical cards, reconciliation and ended-event cleanup.
- cogs/lobby_management.py: host management, participant actions and time proposals.
- database/db.py: lfg_events, lfg_event_members, lfg_event_messages, lfg_voice_notifications and lfg_time_proposals.

Discord cards display state; they are not membership/capacity authority.

## Lifecycle

New event creation exposes exactly **One-time Event** or **Recurring Event**.
Recurring creation opens seven independent Monday–Sunday buttons, requires at
least one weekday, and returns to the same draft before final preview/confirmation.
The first selected date must match a selected weekday; persistence rechecks this
before writing. Back discards weekday changes. Only the draft owner can edit it.
Daily/weekly/weekend/custom primary modes and new finite-end modal settings are
removed. Existing stored V1 frequencies and end conditions remain readable.

This is an interim creation UX improvement. It does not implement V2 persistent
event areas, full series editing or the legacy data cutover. See
[Event Manager V2 status](skills/event-manager-v2.md); V1 live acceptance remains
incomplete, and its materialization horizon limit is still an extraction blocker.

1. `/lfg create`, the managed Looking for Group button and the managed Community Events button all open the same event builder. Validate a future start, optional game, title, capacity and voice reminder; confirm before publishing.
2. Public events appear in the community/per-game flow. Private events use participant-restricted text and a share token/code. Public sharing links to Discord; private sharing can combine a server invite with an event access code.
3. Invitations and joins are distinct persisted states. Final join rechecks active status, private authorization, exclusions and capacity transactionally. Discord access and any required game-role assignment accompany the feature flow.
4. Hosts manage title/note/time/capacity/invites and remove participants through /lfg manage. Joined members can propose a new time; only the host decides. The host cannot simply leave their own active lobby.
5. The 30-second scheduler reconciles cards, creates/claims event voice near the configured lead time and records notifications. Rescheduling resets due-notification state; rescheduling after voice has opened is blocked.
6. Closing/completing and cancellation preserve a final card before cleanup. Cancellation keeps its existing admin override and notifies participants. Occupied voice is retained until empty; terminal cards/private resources are eligible for cleanup after 24 hours. The scheduler also handles overdue sessions.

Keep private tokens, invite codes, participant data and notification state in runtime storage; never log them as debugging context. Role/Discord delivery can fail after a DB transition; preserve retry/reconciliation state rather than recreating an event.

## Validation

[test_lobby_management.py](../tests/test_lobby_management.py) and [test_stability.py](../tests/test_stability.py) cover transactional rules, concurrency and lifecycle behavior. For changes, exercise capacity races, unauthorized/stale actions, private joins, exclusions, rescheduling, missing cards, restart recovery and occupied-voice cancellation as relevant. Live multi-user/permission checks remain in [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md). The Community Events board is an additional entrypoint into this same event lifecycle; Tournaments and Giveaways remain separate informational boards until their own reviewed feature slices.


Public creation may mention the selected game's independent LFG opt-in role once; private events and dashboard refreshes never role-ping. Global Competitive/Casual and LFG Pings roles are not event dependencies. See [role preferences](ROLE_SETTINGS.md).
