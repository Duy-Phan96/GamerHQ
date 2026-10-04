# Existing GamerHQ features → Skill migration map

This document evaluates existing GamerHQ functionality as future Skill candidates.

It is a direction map, not a migration order and not evidence that a feature has already been converted.

## Decision rule

A feature is a strong Skill candidate when it:

- represents user/admin-visible functionality that can be enabled independently;
- owns a clear slice of state and behavior;
- can operate through documented host capabilities;
- can communicate with other features through Events/Public Skill APIs;
- does not need unrestricted access to GamerHQ internals.

Infrastructure that defines the host itself should remain Core/Host functionality.

## Keep in Core / GamerHQ Host

These components are platform infrastructure, not normal installable Skills.

### Authorization / permission boundary

Current examples:

- `services/authorization_service.py`
- shared admin/owner interaction checks
- future capability enforcement

Reason: every Skill depends on trustworthy authorization. A Skill must not own the platform's root authorization model.

### Skill Runtime / SDK

- manifest validation
- lifecycle
- registry/manager
- Event Bus
- Public Skill API Router
- capability contracts
- scheduler contract/runtime
- storage contracts

Reason: this is the platform on which Skills run.

### Persistent storage adapters / migrations

- `database/db.py`
- GamerHQ Skill state/storage adapters

Reason: Skills receive scoped storage capabilities; they do not own the host database connection.

### Server management / setup / health

Current examples:

- `cogs/server.py`
- `cogs/server_management.py`
- `services/health_service.py`
- `services/server_operations.py`
- `services/server_setup_service.py`

Reason: installation, health, recovery and host administration must remain available even when individual Skills are disabled or broken.

### Audit / owner-change infrastructure

Current examples:

- `services/server_log_service.py`
- `services/server_change_observer.py`
- `services/owner_change_feed.py`
- `cogs/owner_changelog.py`

Reason: Skills should emit audit actions into a trusted host-owned audit path rather than control their own root audit trail.

### Generic Discord adapter capabilities

Examples:

- send/edit/delete own messages
- embeds
- roles
- channel/voice operations

Reason: Skills request these through capabilities. Discord.py itself stays behind the host boundary.

## Strong early Skill candidates

These are good candidates after the Runtime, shared Scheduler and Recurring Posts prove the architecture.

### 1. Recurring Posts

Status: planned first reference Skill.

Why it is ideal:

- clear ownership;
- independent enable/disable;
- exercises storage, Discord messaging, scheduler, audit and events;
- useful without depending on other GamerHQ features.

Potential events:

- `recurring-post.created.v1`
- `recurring-post.updated.v1`
- `recurring-post.sent.v1`
- `recurring-post.failed.v1`

### 2. Suggestions

Current implementation:

- `cogs/suggestions.py`

Current behavior already has strong bounded ownership:

- public submission entry;
- private Staff inbox;
- persisted suggestion state;
- status transitions;
- restart recovery;
- permission checks.

Why it fits:

The feature can become an independently enabled workflow without being the host itself.

Likely capabilities:

- `discord.messages.send`
- `discord.messages.edit_own`
- `storage.skill`
- `audit.write`

Potential events:

- `suggestion.created.v1`
- `suggestion.status-changed.v1`
- `suggestion.implemented.v1`

Migration caution:

Private Staff visibility is security-critical. Do not migrate until the generic Discord/channel capability model can preserve effective privacy checks.

### 3. Twitch / Streamer notifications

Current implementation:

- `cogs/twitch_hub.py`
- `services/twitch_service.py`
- `services/streamer_hub_service.py`

Why it fits:

External-provider integration, independent configuration and notification delivery are natural Skill boundaries.

Likely capabilities:

- `http.external`
- `discord.messages.send`
- `storage.skill`
- scheduler/background-task capability
- audit/events

Potential events:

- `streamer.connected.v1`
- `stream.started.v1`
- `stream.ended.v1`
- `stream.notification-sent.v1`

Migration caution:

OAuth tokens are secrets. The future SDK needs isolated Skill secrets; ordinary Skill storage is not a substitute.

### 4. Gaming Deals / Feeds

Current implementation spans:

- `services/gocdkeys_service.py`
- `services/gocdkeys_import_service.py`
- `services/instant_gaming_service.py`
- `services/curated_deal_service.py`
- related deal/import cogs

This should probably become a small Skill family rather than one giant integration.

Possible future Skills:

- Gaming Deals
- Free Games
- Instant Gaming Feed
- GoCDKeys Comparison

Shared contracts could include:

- `deal.discovered.v1`
- `deal.published.v1`
- `game.free-now.v1`

A future Analytics or notification Skill could subscribe without importing feed internals.

### 5. Amazon integration

Current implementation:

- `services/amazon_integration_service.py`

Good candidate because the integration is optional and has a clear permission/config boundary.

Important:

GamerHQ should expose only the host-side Skill/capability integration. Private Amazon product/affiliate implementation may remain a separate product/repository and communicate through an explicit integration contract.

## Good candidates after the first Skills prove the SDK

### Role Preferences / self-service roles

Current areas:

- `services/role_panel_service.py`
- relevant role/profile UI

Potential Skill:

`profile-preferences` or `role-preferences`

It could own optional profile preferences and self-assignable community settings.

Do not move base authorization roles or platform owner/admin checks into this Skill.

Potential events:

- `profile.preference-changed.v1`
- `member.game-role-changed.v1`

### Music Bot integration

Current implementation:

- `services/music_bot_service.py`

This is a relatively bounded integration and could become a Skill once generic role/channel permission capabilities exist.

### Streamer Hub

Streamer profiles/application workflow may eventually be its own Skill, potentially consuming Twitch events from a Twitch integration Skill.

Do not split it prematurely. First decide whether "Twitch integration" and "Streamer Hub" genuinely need separate ownership/contracts.

## High-value but later migrations

These are good long-term Skills but currently deeply coupled to core GamerHQ behavior.

### LFG / Events

Current areas:

- `cogs/lfg.py`
- `cogs/lobby_admin.py`
- `cogs/lobby_management.py`
- `services/lfg_service.py`
- `services/lobby_service.py`
- `services/lobby_dashboard.py`

Long-term Skill:

`events` / `lfg`

Why it is valuable:

It is a major independent product capability and other Skills could consume its Events/Public APIs.

Potential contracts:

- `event.created.v1`
- `event.updated.v1`
- `event.started.v1`
- `event.ended.v1`
- `events.get-event.v1`
- `events.list-upcoming.v1`

Why migrate later:

It currently touches members, game identity, dashboards, scheduling, invites, private/public access, voice creation and recovery. Moving it before the Runtime scheduler/Discord adapters stabilize would create duplicated infrastructure.

### Temporary Voice

Current areas:

- `cogs/voice.py`
- `cogs/voice_controls.py`
- `services/temp_voice_service.py`

Long-term Skill:

`temporary-voice`

Potential events:

- `voice-room.created.v1`
- `voice-room.closed.v1`

Potential Public API:

- `temporary-voice.create.v1` only if another Skill truly needs direct creation.

Why migrate later:

Requires robust channel/permission/member capabilities and coordination with Events/LFG.

### Support Tickets

Current areas:

- `cogs/tickets.py`
- `services/ticket_service.py`
- `services/ticket_entry_service.py`

Long-term Skill:

`support-tickets`

Why it fits:

Independent workflow, persisted lifecycle, Staff actions and clear UI.

Why migrate later:

Privacy and permission correctness are critical. Migration should happen only after the Skill capability model has mature private-channel and member-access contracts.

Potential events:

- `ticket.created.v1`
- `ticket.assigned.v1`
- `ticket.waiting.v1`
- `ticket.closed.v1`

### Games / game profiles

Current areas:

- game catalog
- game roles
- visibility/readiness
- optional game channels
- migrations/recovery

Possible long-term Skill family:

- Game Catalog
- Member Games / Game Profile
- Game Channels

Do not turn this into one huge "Games Skill" automatically.

Why migrate later:

Game identity is currently referenced by LFG, voice, profile roles and server structure. Stable public contracts need to be designed first.

A future neutral contract could expose:

- `games.get-game.v1`
- `games.list-enabled.v1`
- `member.games-changed.v1`

## Probably remain GamerHQ-specific host features

### Server Tour / GamerHQ onboarding

Current implementation is strongly tied to GamerHQ's actual server structure and product journey.

It may use Skills and dynamically show enabled functionality, but it does not need to become a portable third-party Skill unless a real cross-host use case appears.

### Managed GamerHQ boards

`services/managed_message_service.py` currently manages canonical GamerHQ boards and allowlisted GamerHQ actions.

This is not the same product as Recurring Posts.

Keep the current managed-board infrastructure as GamerHQ host/application behavior for now. Later, reusable pieces such as message rendering or safe component contracts can be generalized into capabilities if needed.

### Server structure adoption / repair

These are host ownership/recovery concerns and should remain outside installable Skills.

## Suggested migration order

Do **not** migrate all existing features now.

Recommended sequence:

1. Portable Runtime / SDK
2. Event Bus + Public Skill API Router
3. Persistent shared Scheduler
4. Skills management UI
5. Recurring Posts reference Skill
6. Suggestions
7. one external integration Skill (likely Gaming Deals or Twitch notifications)
8. Role Preferences / Music integration
9. Support Tickets
10. LFG / Events
11. Temporary Voice
12. Games domain decomposition

The exact order after Recurring Posts should be adjusted based on which contracts prove stable.

## Migration rule

An existing feature should migrate only when:

- its required host capabilities exist;
- its state ownership is clear;
- its events/APIs are documented;
- migration is backward compatible;
- restart/recovery behavior has contract tests;
- existing production data can be adopted without destructive conversion;
- the migration reduces coupling instead of merely moving files into `skills/`.

A folder named `skills/` is not modular architecture by itself.
