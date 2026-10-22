# GamerHQ Thin Host Inventory

_Last reviewed: 2026-10-12_

_Repository snapshot: `develop` at `bcc218165639cdbe798682cc2dfb7c0a1d79e260`, Host version `1.0.0-beta.2-dev`._

This inventory classifies the current GamerHQ-owned implementation before the
next Skill extraction is selected. It is an architecture decision aid, not a
code-movement plan and not a second product backlog.

GamerHQ remains the first/reference customer of the platform. The desired
long-term direction is:

```text
GamerHQ Discord community
        -> thin Host and safe Discord adapters
        -> public Runtime / SDK / capabilities
        -> independently released installed Skills
```

Extraction must reduce Host-specific product logic without moving Host
primitives, duplicating authoritative state or freezing an unstable boundary.

## Scope and method

The review covered the meaningful domain groups currently implemented under:

- `bot.py` and `config.py`;
- `cogs/`;
- `services/`;
- `database/`;
- `hosts/gamerhq/`;
- `skill_runtime/`;
- the current external Skill lock and architecture documentation.

The assessment uses current code as the strongest source of truth. Module size
alone is not evidence that something should become a Skill.

No code, database schema, Skill package, runtime contract or production resource
is moved by this inventory.

## Classification

### HOST PRIMITIVE

Behavior that must remain owned by the Host/platform because it enforces trust,
connects to Discord or provides generic runtime infrastructure.

### KEEP TEMPORARILY

Product/domain behavior that may later move, but whose public contract, storage,
Discord interaction model or migration boundary is not stable enough now.

### SKILL CANDIDATE

A coherent, independently useful domain that could become a standalone Skill
after explicit extraction-readiness work.

### LEGACY / RETIRE

Compatibility, migration or provider-specific historical behavior that must not
be promoted into a new permanent platform contract. It remains only while safe
migration/history support requires it.

## Current platform boundary

The existing public Skill boundary already supports:

- namespaced Skill storage and encrypted Skill secrets;
- scheduler jobs and read-only job inspection;
- public events and Skill-to-Skill APIs;
- public Management APIs and declarative Management UI schemas;
- external HTTPS requests;
- audit writes;
- member/channel reads;
- role grant/removal;
- owned message send/edit/delete, embeds and validated link buttons.

The current public boundary does **not** provide portable registration for
Discord slash commands, buttons or modals, nor generic category/channel
creation, permission-overwrite management, private-channel lifecycle or native
onboarding administration. Those omissions are important: tickets, suggestions,
LFG, voice and native onboarding cannot simply be copied into an external
repository without either redesigning them or adding reviewed Host contracts.

`skills.first_party_skills()` currently returns no built-in portable Skills.
Reviewed portable product implementations are installed as independent packages
through immutable pins instead. Recurring Posts, XP & Progression and Awin
Affiliate are the current reference examples.

# Ownership inventory

| Domain / module group | Current responsibility and evidence | Classification | Dependencies and extraction blockers | Recommended sequence / repository boundary |
| --- | --- | --- | --- | --- |
| Runtime contracts and SDK (`skill_runtime/contracts/`, `skill_runtime/devtools.py`) | Defines manifests, capabilities, context ports, events, scheduler, Management/Public APIs and schema contracts. | **HOST PRIMITIVE** | Public compatibility and security boundary. Moving it into a product Skill would invert ownership. | Keep platform-owned. Version independently from GamerHQ product Skills. |
| Runtime execution (`skill_runtime/runtime/`) | Registry, lifecycle, event delivery, API routing, scheduler contracts and runtime enforcement. | **HOST PRIMITIVE** | Must enforce isolation and lifecycle for every installed Skill. | Keep platform-owned; change only for concrete reviewed Host requirements. |
| GamerHQ Host adapters (`hosts/gamerhq/skill_*`) | Implements Discord, storage, scheduler, secrets, HTTP, package loading and deployment-plan ports against GamerHQ/SQLite/Discord.py. | **HOST PRIMITIVE** | Contains trusted implementation of public capabilities and immutable package loading. | Keep Host-owned. Product Skills consume these ports but never import them. |
| Internal web transport (`hosts/gamerhq/web_api.py`, `web_platform.py`) | Trusted Host-side API/BFF transport over authoritative Skill/runtime state. | **HOST PRIMITIVE** | Authentication, guild authorization and state authority belong to the Host. Web UX belongs to `gamerhq-web`. | Keep only public Host/API contracts here; web implementation changes require a handoff. |
| Bot startup, Gateway and process lifecycle (`bot.py`, `config.py`) | Discord connection, intents, extension loading, command sync, Skill bootstrap, scheduler start and one-active-process lifecycle. | **HOST PRIMITIVE** | Direct Discord/process trust boundary. | Keep Host-owned. Domain listeners should increasingly emit stable events instead of calling a specific Skill privately. |
| Authorization, operation context and response safety (`authorization_service`, `operation_context`, shared response/error helpers) | Rechecks current membership/permissions, bounds reads and centralizes safe interaction errors/metrics. | **HOST PRIMITIVE** | Security and reliability policy must remain authoritative across all domains. | Keep Host-owned and reusable by adapters/management surfaces. |
| Diagnostics, release, backup and recovery (`health_service`, `server_log_service`, `tools/`, `scripts/`, release/deploy docs) | Read-only health, preflight, repository audit, backup, migration rehearsal, deployment and rollback. | **HOST PRIMITIVE** | Operates on Host process, package set and authoritative runtime database. | Keep Host-owned; Skills expose bounded diagnostics through public contracts. |
| Server observation and owner change history (`cogs/server_changes.py`, `cogs/owner_changelog.py`, channel/server change services) | Observes Discord resource drift, records changes, provides reviewed adoption/undo and protects private access. | **HOST PRIMITIVE** | Requires Gateway events, desired-state ownership and cross-domain safety decisions. | Keep Host-owned. Skills may receive stable public signals but must not own global server observation. |
| Generic managed-resource machinery (`server_service`, `managed_message_service`, reconciliation and safe permission helpers) | ID-based ownership, message recovery, preview/confirm, duplicate review and safe Discord mutation primitives. | **HOST PRIMITIVE** | Enforces ownership and protects unrelated/custom Discord resources. | Keep generic machinery in Host. Do not equate GamerHQ-specific copy/layout with the primitive itself. |
| GamerHQ server blueprint, boards and copy (`server_setup_service`, `community_structure_service`, command guides and Server Management UI) | Defines GamerHQ's START HERE, COMMUNITY, EVENTS, MARKETPLACE and STAFF desired state and managed messages. | **KEEP TEMPORARILY** | Mixes reference-customer configuration with generic managed-resource primitives. Empty-server/default configuration model is not yet separated cleanly. | Keep while GamerHQ remains the reference customer. Gradually move static customer defaults toward configuration; do not create a “server blueprint Skill” yet. |
| Games, game roles and optional game channels (`cogs/games.py`, selectors/channels, `game_*` and role services, game tables in `database/db.py`) | Owns catalog, selection roles, visibility, optional channel creation/recovery, thresholds, migration and role safety. | **KEEP TEMPORARILY** | Core GamerHQ product loop; highly coupled to native roles, optional channels, onboarding, LFG preferences, legacy areas and server desired state. Public channel/permission lifecycle is not available to Skills. | Stabilize and simplify inside GamerHQ first. Later split catalog/profile signals from Discord resource management before reconsidering extraction. |
| Member onboarding, role preferences, profile and Booster projection (`cogs/roles.py`, `cogs/profile.py`, role/panel/onboarding/profile/booster services) | Manages native Discord roles/onboarding and projects public profile data from roles, games and native boost state without duplicating member truth. | **KEEP TEMPORARILY** | The profile is currently an aggregation over Host/GamerHQ state, not a self-owned Skill store. Native onboarding and role-resource lifecycle are not public Skill capabilities. | Keep together until public profile signals/APIs and ownership of editable profile fields are explicit. Do not extract the read-only projection alone. |
| Event Manager / LFG and scheduled events (`cogs/lfg.py`, lobby admin/management, lobby/LFG services and event tables) | Public/private event creation, participant state, invites, share links, host management, cards, reminders and event voice lifecycle. Recurring event series are the next planned capability. | **SKILL CANDIDATE — PLANNED AFTER RECURRING EVENTS** | Core product value with rich components, private channel permissions, event voice and recovery. Before extraction, recurring series must be timezone/DST-safe, restart-idempotent and backward compatible; stable public event lifecycle contracts must become the authoritative boundary. | Implement and live-accept recurring events in GamerHQ first (Issue #153), then separate Event Manager domain state from Discord adapters, perform extraction-readiness review and hand off to a standalone Event Manager Skill repository. GamerHQ remains the first/reference customer; tournament specialization remains deferred. |
| Temporary voice (`cogs/voice.py`, `voice_controls.py`, `temp_voice_service.py`) | Voice-state creation, persisted ownership, rename/limit/lock/invite/remove/close and empty cleanup. | **KEEP TEMPORARILY** | Gateway observation and cleanup are Host concerns; current room policy uses direct Discord permissions and GamerHQ music/staff rules. Public Skill context does not expose this lifecycle. | Keep until Gateway signal versus room-policy ownership can be separated safely. Do not extract by copying direct Discord.py access. |
| Private support tickets (`cogs/tickets.py`, `ticket_service.py`) | Durable private ticket creation, assignment, waiting, close/history, audit and staff overview. | **KEEP TEMPORARILY** | Domain is mature, but it requires private channel creation, overwrite verification, persistent component registration and current Staff policy—all absent from the portable Skill boundary. Privacy raises the cost of a premature contract. | Keep through live core-beta acceptance. Reconsider only after a narrowly reviewed private-resource/interaction contract exists for more than one concrete Skill. |
| Suggestions (`cogs/suggestions.py`) | Member modal submission, durable dedupe, private Staff inbox, restart recovery and review-state transitions. | **SKILL CANDIDATE** | Clear domain and self-contained state, but needs portable modal/button registration and a safe private Staff destination contract. Current implementation directly owns Discord components and channel privacy checks. | Candidate after interaction/private-destination capabilities are justified by multiple use cases. Not the next extraction because it would force a broad new Host contract first. Separate repository/handoff required when ready. |
| Streamer/Twitch beta (`cogs/streamer.py`, `cogs/twitch_hub.py`, streamer/Twitch services, `database/twitch.py`) | Approved-streamer role/resources, Device OAuth, EventSub/live delivery and persisted connections. | **SKILL CANDIDATE** | Independently useful, but still a disabled hidden beta with GamerHQ guild/config assumptions, direct role/channel lifecycle and limited live acceptance. OAuth/EventSub ownership and managed-resource contracts need stabilization. | Keep disabled/isolated until live beta evidence exists. Later extract to a Creator/Streamer Skill through a separate repository and explicit handoff. |
| Curated promotions and manual deal delivery (`cogs/gocdkeys.py`, `deal_editor.py`, `deal_import.py`, curated/GoCDKeys services, `database/affiliate_deals.py`) | Admin preview/confirm, validated one-off deal/giveaway cards, normalized-link import and durable at-most-once delivery/history. | **SKILL CANDIDATE — RECOMMENDED NEXT READINESS TARGET** | Clear independent value and strong dedupe semantics. Core can fit existing storage, Management API, channel-read, message/embed/link-button and audit capabilities. Current code still imports Discord.py, `config.GUILD_ID`, GamerHQ target resolution and provider-specific URL rules; legacy storage is shared with older integrations. | Perform an extraction-readiness slice in GamerHQ first. A new repository is created only after scope, contracts and migration are approved. |
| Awin Affiliate Discord management (`cogs/awin_management.py`) | Host-side UI adapter calling only stable public APIs of the already external Awin Skill. | **KEEP TEMPORARILY** | Implementation is external, but the provider-specific Host UI is large. Generic schema rendering is not yet sufficient for every Awin workflow. | Keep as an adapter, never import Awin internals. Gradually replace provider-specific presentation with generic Management UI capabilities when proven. Awin releases remain independent. |
| Progression activity adapter (`cogs/progression_activity.py`) | Samples GamerHQ voice/LFG state and calls public Management contracts on the external Progression Skill. | **KEEP TEMPORARILY** | Progression implementation is already external, but Host domain signals are still wired directly to one Skill contract. | Replace direct Skill-specific calls with stable generic activity/event signals only when contracts are reviewed. Do not move Gateway/LFG observation into Progression. |
| Amazon, Instant Gaming and provider-specific Host integrations | Existing manual links, external-bot channel support, provider validation and historical marketplace behavior. | **LEGACY / RETIRE** as Host product logic; future provider behavior belongs in independent Skills/integrations. | Provider programs have independent terms, credentials, release cycles and compliance. Some code remains required for current server compatibility/history. | Preserve compatibility until reviewed replacements exist. Do not make provider-specific behavior a permanent Host API. Use separate repository handoffs for future Amazon/Instant Gaming Skills. |
| Marketplace Electricity and historical finance/energy migrations (`support_service`, legacy finance/partner migration paths and historical ticket types) | GamerHQ-specific partner boards, Germany-only electricity request flow and safe retirement/history of older finance/energy resources. | **LEGACY / RETIRE** for platform purposes; current customer configuration remains supported. | These flows encode GamerHQ-specific commercial copy, channels and migration history rather than reusable Host infrastructure. Historical tickets/mappings cannot be deleted blindly. | Maintain safe reads/closure/migration until no longer needed. Do not extract historical compatibility as a new Skill. A future electricity product would require a clean independent design/repository. |
| Legacy game areas, old streamer area commands and disabled automatic provider paths | Migration evidence, compatibility readers and explicit cleanup for superseded structures/features. | **LEGACY / RETIRE** | Still referenced by recovery/history and cannot be deleted by assumption. Not a valid foundation for new product contracts. | Characterize references, retire only through focused migrations and preserve unknown/custom resources. Never package as Skills. |

# Extracted reference implementations

These domains are no longer built-in product implementations:

| External Skill | Current role in GamerHQ |
| --- | --- |
| Recurring Posts | Independent package pinned immutably; reference for scheduler, edit/review and Management UI contracts. |
| XP & Progression | Independent package pinned immutably; GamerHQ retains only Host activity adapters/signals. |
| Awin Affiliate | Independent provider package pinned immutably; GamerHQ retains a public-contract-only Discord management adapter. |

Their repositories own their code, tests, versions and releases. GamerHQ only
reviews and pins an immutable version when it chooses to consume one.

# Recommended next extraction-readiness candidate

## Curated Promotions / Deals core

The next readiness target should be the **provider-neutral curated promotions
core**, not the complete GamerHQ Marketplace and not every Amazon/Instant
Gaming/GoCDKeys compatibility path.

Recommended product boundary:

> An administrator configures a destination, previews a one-off promotion or
> giveaway, confirms delivery, and receives durable at-most-once status/history.

### Why this candidate is first

1. **Clear independent value** — many communities need controlled one-off deal,
   partner or giveaway posts without a full affiliate provider integration.
2. **Existing safety behavior** — preview/confirm, URL validation, durable claims,
   uncertain-send handling and duplicate prevention already exist.
3. **Good capability fit** — the portable boundary already provides Skill
   storage, Management APIs/UI schemas, channel reads, messages, embeds, link
   buttons and audit writes.
4. **Lower Host-contract pressure** — unlike tickets, suggestions, LFG or voice,
   it does not require portable slash-command/component registration or private
   channel creation if management remains schema/API driven.
5. **Reduces provider coupling** — generic delivery can leave Amazon, Awin,
   Instant Gaming and GoCDKeys credentials/rules in their independently owned
   integrations.
6. **Proven neighboring patterns** — Recurring Posts demonstrates managed
   message scheduling, and Awin demonstrates external provider ownership.

### Explicitly excluded from this candidate

- product scraping or unsupported automated price lookup;
- Awin-owned Creative Library/campaign behavior;
- Amazon/Instant Gaming tracking-ID or credential ownership;
- GamerHQ MARKETPLACE category/channel creation and permission policy;
- recurring scheduling already owned by Recurring Posts or provider Skills;
- historical finance/electricity migrations;
- tournament/event giveaway mechanics;
- arbitrary third-party HTML/JavaScript.

## Readiness work required before a standalone repository

### 1. Freeze scope and identity

Define one stable Skill ID/name and V0.x responsibility. Do not create the
standalone repository until the owner approves that boundary.

### 2. Separate generic domain data from provider compatibility

Inventory `curated_deals` and `processed_affiliate_deals` usage. Decide which
records belong to the new generic domain and which remain provider/legacy
history. Do not migrate all rows by table name alone.

### 3. Define namespaced storage and migration

Document:

- promotion/delivery IDs;
- normalized dedupe identity;
- preview versus persisted state;
- delivery statuses (`claimed`, `posted`, `uncertain`, etc.);
- migration and rollback from current SQLite tables;
- how already posted messages remain readable without redelivery.

The new Skill must not directly import `database.db` or
`database.affiliate_deals`.

### 4. Freeze public Management contracts

At minimum, evaluate versioned contracts for:

- configuration read/update with revision;
- create/edit preview;
- confirmed publish;
- list/get history;
- safe retry/recovery of uncertain delivery;
- delete/retire semantics only where a durable object actually exists.

Persistent configuration must expose Review/Confirm, Back/Cancel and stale
revision handling.

### 5. Use existing public capabilities first

Expected capabilities should remain bounded to the current public set, likely:

- `discord.channels.read`;
- `discord.messages.send`;
- `discord.embeds.send`;
- `storage.skill`;
- `audit.write`;
- optionally `skills.api.call` for explicitly reviewed provider composition.

Do not add channel creation, permission management or arbitrary interaction code
for this extraction.

### 6. Remove Discord.py and GamerHQ target assumptions from the core

Portable rendering must produce host-neutral message/embed/link-button payloads.
The Skill configuration stores an explicit reviewed destination channel ID.
GamerHQ-specific `GUILD_ID`, marketplace aliases, Staff role discovery and
managed `gaming-deals`/`giveaways` resolution remain outside the Skill.

### 7. Define provider composition without merging ownership

Provider Skills may later call a small public promotion API or send their own
messages. The readiness slice must choose one approach and avoid two competing
delivery truths. Awin remains authoritative for Awin campaigns; an Amazon Skill
remains authoritative for Amazon-specific credentials/links.

### 8. Add extraction gates

Before repository handoff:

- stable ID, storage keys and Management contract IDs;
- full offline tests on Python 3.12/3.14;
- capability/source audit;
- no private GamerHQ imports;
- migration rehearsal on synthetic existing data;
- retry/restart and uncertain-send tests;
- README, design, changelog and release checklist;
- independent package build/conformance;
- immutable install/pin path;
- explicit removal plan for the duplicate built-in implementation.

# Recommended sequence

```text
1. Merge and use the core-beta acceptance procedure for the next exact RC.
2. Complete a GamerHQ-local Curated Promotions extraction-readiness slice.
3. Review scope, storage migration, contracts and capability fit.
4. Owner decides whether to create a standalone repository.
5. GamerHQ produces a handoff prompt; the new repository owns implementation.
6. Standalone Skill releases independently with green CI/conformance.
7. GamerHQ reviews and pins an immutable version on its own branch/PR.
8. Migrate compatible state with backup/rehearsal and remove duplicate built-in
   behavior only after acceptance.
```

Suggestions, Streamer/Twitch, Tickets, Member Profile, LFG and Temporary Voice
remain future candidates. They should not be started in parallel with the
Curated Promotions readiness work.

# Decision

**Proceed with Curated Promotions / Deals extraction-readiness next, but do not
create or populate a new Skill repository yet.**

The readiness slice belongs in GamerHQ because GamerHQ currently owns the code
and migration state. Once the boundary is approved, implementation moves by
prompt-based handoff to one independent repository. No other repository is
modified from this project.

## Maintenance rule

Update this inventory after a material Host/SDK capability change, completed
extraction or major domain redesign. Day-to-day work remains in GitHub Issues and
pull requests; this file must not become a duplicate task board.
