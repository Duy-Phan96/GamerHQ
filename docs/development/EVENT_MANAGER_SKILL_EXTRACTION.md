# Event Manager Skill Extraction Readiness

_Status: PREPARATION — extraction begins only after Recurring Events V1 live acceptance._

## Goal

Move the Event Manager out of GamerHQ over time into an independently released Skill while keeping GamerHQ as the first/reference customer.

The Skill must own Event Manager domain behavior without importing Discord.py or private GamerHQ Host implementation modules.

## Domain that is now separable

The following behavior is already conceptually portable:

- recurrence definition;
- daily/weekly/selected-weekday scheduling;
- IANA timezone and DST handling;
- series persistence model;
- occurrence materialization;
- occurrence indexes and series linkage;
- occurrence-vs-series edit semantics;
- series split semantics;
- terminal series semantics;
- rolling materialization horizon;
- retry/restart idempotency.

## Behavior that remains Host-coupled

The current implementation still depends on GamerHQ Host code for:

- slash-command registration;
- buttons, selects and modals;
- public event-card publication;
- persistent Discord component registration;
- private text-channel creation and permission overwrites;
- Discord invite generation;
- event voice-channel creation and permission overwrites;
- member/role checks and game-role integration;
- direct-message delivery;
- Discord Gateway lifecycle/cache;
- cleanup of Discord-owned event resources;
- current Staff/authorization policy;
- direct SQLite tables under the Host database.

## Required public contracts before full extraction

The next extraction review should decide whether to expose, reuse or redesign contracts for:

1. interaction registration and routing;
2. managed public event-card surfaces;
3. private event destination/channel lifecycle;
4. temporary event voice lifecycle;
5. participant/member lookup and authorization;
6. outbound notifications/DMs;
7. event lifecycle signals;
8. Skill-owned persistent storage and migration from current Host tables.

Do not add a broad Host capability solely for Event Manager unless at least one additional Skill/domain can justify the same generic contract.

## Proposed future repository boundary

Standalone repository concept:

`gamerhq-skill-event-manager`

Candidate internal ownership:

- recurrence domain;
- event/series domain state;
- occurrence materialization;
- event commands/actions as portable intent handlers;
- management API/schema;
- tests/conformance fixtures;
- migration adapter for legacy GamerHQ state.

Host ownership should remain:

- Discord connection;
- trusted interaction dispatch;
- channel/permission enforcement;
- Gateway observation;
- secrets/storage/runtime primitives;
- generic audit/diagnostics;
- host-side Discord adapters.

## Migration strategy

1. Freeze the accepted recurring-event semantics.
2. Define a versioned Event Manager domain/API contract.
3. Add conformance tests in GamerHQ before moving code.
4. Introduce Skill-owned storage with explicit migration of existing series/events.
5. Dual-read/verify only if required for migration safety; avoid dual authority.
6. Switch GamerHQ to the installed Skill as the authoritative Event Manager.
7. Remove legacy Host domain code only after production acceptance.
8. Release the Skill independently from the Host.

## Data migration requirements

Current authoritative data includes:

- `lfg_event_series`;
- `lfg_events`;
- `lfg_event_members`;
- `lfg_event_messages`;
- `lfg_voice_notifications`;
- `lfg_time_proposals`.

Migration must preserve:

- stable event identity where externally referenced;
- host/participant state;
- terminal history;
- recurring series boundaries;
- occurrence indexes;
- private/public visibility;
- message/channel mappings needed for safe Discord recovery.

## Extraction blockers

Before creating/moving production implementation to the standalone Skill repository:

- [ ] Recurring Events V1 live acceptance completed.
- [ ] Interaction/Discord-resource contract decision recorded.
- [ ] Storage ownership/migration design approved.
- [ ] Event lifecycle public signals/API defined.
- [ ] GamerHQ adapter responsibilities documented.
- [ ] Conformance tests cover restart, retry, DST, privacy and series split.
- [ ] Release/version policy for the Event Manager Skill defined.

## Recommended next decision

After live acceptance, create a dedicated handoff to the future Event Manager Skill repository containing:

- accepted V1 behavior;
- domain contracts;
- migration plan;
- required Host capabilities;
- conformance tests;
- explicit non-goals.

Issue #153 remains the parent tracking item until that extraction-readiness decision is recorded.
