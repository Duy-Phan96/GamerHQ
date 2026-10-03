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
Scoped Discord message capability adapter with per-Skill message ownership, safe mention defaults and explicit embed/everyone capabilities.

See [Discord message capabilities](discord-capabilities.md) for the host contract and ownership guarantees.

### Later
Skills management UI, then Recurring Posts as the first reference Skill.
