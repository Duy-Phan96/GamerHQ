# Skill Runtime architecture

## Goal

The Skill Runtime is designed as a portable platform component. GamerHQ is its first host, not its permanent boundary.

The intended dependency direction is:

```text
Skills
  ↓
Portable Skill Runtime / SDK
  ↓
Host ports
  ↓
GamerHQ host adapters
  ↓
Discord.py / SQLite / GamerHQ services
```

The dependency direction must never reverse. `skill_runtime/` must not import `database`, `services`, `cogs`, Discord.py, or GamerHQ-specific business logic.

## Portable runtime

The portable layer owns:

- Skill manifests and compatibility rules
- stable capability IDs
- Skill lifecycle contracts
- event contracts
- Public Skill API contracts
- SkillContext port definitions
- SkillRegistry
- SkillManager
- typed Event Bus
- Public Skill API router
- scoped Skill-facing Event/API adapters
- shared scheduler contracts and execution engine

It deliberately does not decide which database, Discord library, scheduler persistence implementation, or audit UI a host uses.

## GamerHQ host

`hosts/gamerhq/` is the first implementation of the host ports.

It may use existing GamerHQ infrastructure, including:

- SQLite through `database.db`
- current authorization rules
- the private Server Log
- Discord.py adapters
- the persistent Skill scheduler store

Host-specific behavior must stay outside the portable runtime.

## Persistence

GamerHQ currently provides additive tables:

### `skill_guild_state`

Stores per-guild enablement and the installed Skill version.

The SkillManager accesses this only through `SkillStateStorePort`.

### `skill_storage`

Stores JSON values under the compound namespace:

```text
guild_id + skill_id + storage_key
```

A Skill never receives a raw database connection. It receives a storage port already scoped to its own namespace.

### `skill_jobs`

Stores host-owned persistent scheduler jobs. Portable Skill code never queries this table directly.

Jobs are keyed by:

```text
guild_id + skill_id + job_key
```

The GamerHQ adapter provides atomic claim leases, claim tokens and revision checks so overlapping scheduler workers cannot silently duplicate or overwrite edited jobs.

## Capability enforcement

Manifest permissions are not just installation text. Host adapters must enforce them at the capability boundary.

Example:

A Skill without:

```text
storage.skill
```

must receive a permission error when attempting storage access even if its Python code obtains a storage-port reference.

The same rule applies to scheduler jobs, Discord messaging, external HTTP access, Events and Public Skill APIs.

## Extraction path

A future extraction should primarily consist of moving:

```text
skill_runtime/
docs/skills/
SDK test utilities
```

into a dedicated SDK/runtime package.

The GamerHQ-specific adapter remains in GamerHQ and depends on that package.

This is why public Runtime contracts must never contain:

- GamerHQ channel names
- GamerHQ database table assumptions
- GamerHQ command names
- concrete Discord.py objects
- the Discord bot token
- private implementation types from another Skill

## Current implementation slices

### Slice A
Portable contracts: manifest, capabilities, lifecycle, context ports, events and Public Skill APIs.

### Slice B
Portable SkillRegistry and per-guild SkillManager lifecycle.

### Slice C
First GamerHQ host adapters: persistent enablement, namespaced JSON storage, capability enforcement and Server Log audit adapter.

### Slice D
Typed Event Bus and Public Skill API router. Both validate manifest declarations. Optional host availability checks prevent disabled Skills from receiving events or serving/calling Public APIs.

### Slice E
Shared persistent Scheduler contracts/engine plus the GamerHQ SQLite host adapter. V1 supports one-shot, interval, daily and weekly schedules with explicit timezone behavior, persistent restart recovery, atomic leases and revision-safe completion.

See [Skill Scheduler](scheduler.md) for the scheduler contract and execution guarantees.

### Slice F
Guild-scoped Discord capability adapter plus `/server manage → Skills`.

Portable Skills can inspect allowed channel metadata and send messages only through
explicit capabilities. Discord.py exceptions are translated into host-neutral
errors, and declared capabilities are validated against what the GamerHQ host
actually provides before activation.

The management UI reads the existing Skill registry and `skill_guild_state`.
It does not create a second enablement store. Disabling a Skill therefore also
continues to use the scheduler's existing disabled-Skill gate.

### Slice G
Recurring Posts is the first complete reference Skill.

It uses only public Runtime surfaces:

- process-level `SkillRegistrationContext` for stable scheduler/API handlers;
- guild-scoped `SkillContext` for execution;
- namespaced Skill storage;
- the shared persistent scheduler;
- narrow Discord capabilities;
- owner-controlled `/server manage → Skills` configuration.

No Skill-owned scheduler loop or database table is introduced.

The host starts one shared Scheduler worker. Handler execution reconstructs a
fresh guild-scoped SkillContext so process-level registration never leaks a
Discord Guild/client into portable Skill code.

See [Recurring Posts](recurring-posts.md).


## External Skill package boundary

Independent Skill repositories are discovered through installed Python entry
points in the `gamerhq.skills` group.

Package discovery is deliberately separate from package installation:

```text
reviewed deployment installs package
        ↓
explicit GAMERHQ_EXTERNAL_SKILLS allowlist
        ↓
installed entry-point discovery
        ↓
SkillRegistry validation
        ↓
normal capability/lifecycle enforcement
```

The runtime never clones a Git repository or runs package installation from a
Discord interaction.


## Host management contracts

Portable Skills may expose versioned Management APIs for trusted host
administration surfaces.

Dependency direction:

```text
GamerHQ management UI
        ↓
SkillManagementRouter
        ↓
versioned Management API
        ↓
fresh guild-scoped SkillContext
        ↓
portable Skill implementation
```

This prevents host UI code from importing an external Skill's private Python
implementation merely to configure it.

Management APIs are not Skill-to-Skill APIs and do not grant another Skill
access to administrative operations.
