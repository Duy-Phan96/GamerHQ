# LFG events and active lobbies

## Code ownership

- cogs/lfg.py: creation/preview/join/share UI, Discord role assignment, private text/voice, notifications, persistent views and scheduler.
- services/lfg_service.py: game eligibility, lookups, rendering and Europe/Berlin date parsing; ambiguous/nonexistent DST times are rejected.
- services/lobby_service.py: transactional host/member rules. BEGIN IMMEDIATE serializes final capacity/state checks.
- services/lobby_dashboard.py: event locks, canonical cards, reconciliation and ended-event cleanup.
- cogs/lobby_management.py: host management, participant actions and time proposals.
- database/db.py: lfg_events, lfg_event_members, lfg_event_messages, lfg_voice_notifications and lfg_time_proposals.

Discord cards display state; they are not membership/capacity authority.

## Lifecycle

1. /lfg create and the managed LFG button use the event builder. Validate a future start, game, title, capacity and voice reminder; confirm before publishing.
2. Public events appear in the community/per-game flow. Private events use participant-restricted text and a share token/code. Public sharing links to Discord; private sharing can combine a server invite with an event access code.
3. Invitations and joins are distinct persisted states. Final join rechecks active status, private authorization, exclusions and capacity transactionally. Discord access and any required game-role assignment accompany the feature flow.
4. Hosts manage title/note/time/capacity/invites and remove participants through /lfg manage. Joined members can propose a new time; only the host decides. The host cannot simply leave their own active lobby.
5. The 30-second scheduler reconciles cards, creates/claims event voice near the configured lead time and records notifications. Rescheduling resets due-notification state; rescheduling after voice has opened is blocked.
6. Closing/completing and cancellation preserve a final card before cleanup. Cancellation keeps its existing admin override and notifies participants. Occupied voice is retained until empty; terminal cards/private resources are eligible for cleanup after 24 hours. The scheduler also handles overdue sessions.

Keep private tokens, invite codes, participant data and notification state in runtime storage; never log them as debugging context. Role/Discord delivery can fail after a DB transition; preserve retry/reconciliation state rather than recreating an event.

## Validation

[test_lobby_management.py](../tests/test_lobby_management.py) and [test_stability.py](../tests/test_stability.py) cover transactional rules, concurrency and lifecycle behavior. For changes, exercise capacity races, unauthorized/stale actions, private joins, exclusions, rescheduling, missing cards, restart recovery and occupied-voice cancellation as relevant. Live multi-user/permission checks remain in [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md); EVENTS tournament/giveaway boards are a different, currently informational feature.


Public creation may mention the selected game's independent LFG opt-in role once; private events and dashboard refreshes never role-ping. Global Competitive/Casual and LFG Pings roles are not event dependencies. See [role preferences](ROLE_SETTINGS.md).
