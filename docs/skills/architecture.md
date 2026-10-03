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

It deliberately does not decide which database, Discord library, scheduler implementation, or audit UI a host uses.

## GamerHQ host

`hosts/gamerhq/` is the first implementation of the host ports.

It may use existing GamerHQ infrastructure, including:

- SQLite through `database.db`
- current authorization rules
- the private Server Log
- Discord.py adapters
- future shared scheduler infrastructure

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

## Capability enforcement

Manifest permissions are not just installation text. Host adapters must enforce them at the capability boundary.

Example:

A Skill without:

```text
storage.skill
```

must receive a permission error when attempting storage access even if its Python code obtains a storage-port reference.

The same rule will apply to Discord messaging, scheduler jobs, external HTTP access, and future privileged capabilities.

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

### Later
Event bus, Public Skill API router, shared scheduler, Discord adapter, Skills management UI, then Recurring Posts as the first reference Skill.
