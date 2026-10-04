# GamerHQ Skill Authoring Contract

This file is the strict construction contract for humans, code generators and AI systems producing GamerHQ-compatible Skills.

If this contract conflicts with an example, this contract wins.

## Required invariants

A generated or manually written Skill MUST:

1. use a stable lowercase kebab-case `SkillManifest.id`;
2. use semantic versioning;
3. declare a supported `runtime_api_version`;
4. request only known capabilities actually required by the implementation;
5. expose an external package through `gamerhq.skills` when distributed separately;
6. make the entry-point name exactly equal the manifest Skill ID;
7. return a new Skill instance from a zero-argument factory;
8. implement `register`, `enable`, `disable`, `start`, `stop`, and `health_check`;
9. use only public `skill_runtime` contracts for portable host access;
10. use `SkillRegistrationContext` only for process-level handler registration;
11. use `SkillContext` for guild-scoped work;
12. use `storage.skill` instead of raw database access;
13. use `scheduler.jobs` instead of a private persistent scheduling loop;
14. use versioned Event/API/handler IDs;
15. remain testable without a Discord token;
16. fail closed when required capability/resource access is unavailable;
17. keep health checks read-only;
18. keep disable non-destructive by default;
19. avoid leaking private exception text or secrets;
20. document every public dependency surface.

## Forbidden dependencies

Portable Skill code MUST NOT import or access:

```text
bot
cogs
config
database
hosts
services
discord
discord.py
.env
raw bot tokens
another Skill's private implementation
```

A host capability replaces direct access to host implementation details.

## Required lifecycle semantics

### register(registration_ctx)

MUST be process-global and idempotent.

MAY register:

- scheduler handlers;
- Public Skill API handlers.

MUST NOT:

- inspect a guild;
- send Discord messages;
- write guild configuration;
- start a private persistent scheduler;
- capture host-specific objects.

### enable(ctx)

MUST be guild-scoped and retry-safe.

MUST NOT assume this is first installation.

### start(ctx)

MUST be guild-scoped.

MAY attach subscriptions needed while the Skill is running.

### stop(ctx)

MUST release guild-scoped runtime activity.

MUST tolerate shutdown.

### disable(ctx)

MUST stop future Skill activity through normal Runtime gates.

MUST NOT erase configuration or user data unless the user separately requested a destructive reset/uninstall operation.

### health_check(ctx)

MUST NOT mutate state.

## Contract selection rules

Use a Capability when:

> The Skill needs the host to do something.

Use an Event when:

> The Skill announces that something happened.

Use a Public Skill API when:

> Another Skill requires a direct request/response result.

Use Skill Storage when:

> State belongs privately to this Skill.

Use Scheduler Jobs when:

> Work must execute later or recur and survive restart.

## Scheduler rules

Every persisted scheduled operation MUST have:

- stable Skill-owned job key;
- stable versioned handler ID;
- small JSON payload;
- documented retry behavior.

Do not claim exactly-once delivery for external systems unless the Skill independently implements and proves that guarantee.

## Persistence rules

Stored payloads SHOULD include a schema version when evolution is expected.

Migration MUST:

- be deterministic;
- preserve unrelated state;
- fail safely on malformed data;
- avoid destructive reset as the default recovery path.

## Public contract naming

Examples:

```text
my-skill.completed.v1
my-skill.get-status.v1
my-skill.cleanup.v1
```

Changing the meaning/schema of an existing versioned contract requires a new version.

## Security defaults

Generated code MUST default to least privilege.

If uncertain whether a capability is needed, do not add it.

If uncertain whether an operation is safe or destructive, require explicit review/confirmation or leave it unimplemented.

If a required host surface does not exist, do not bypass the SDK. The correct outcome is to request/define a new host capability.

## Output expected from an AI Skill generator

Before writing implementation code, produce:

1. Skill ID;
2. purpose;
3. package name;
4. Runtime API version;
5. exact required capabilities and why;
6. storage schema;
7. scheduler handlers/jobs;
8. Events;
9. Public APIs;
10. configuration flow;
11. security/privacy considerations;
12. failure/retry model;
13. test plan.

Then implement only the approved surfaces.

## Validation checklist

Before declaring completion, verify:

- entry-point ID == manifest ID;
- no forbidden imports;
- all used capabilities are declared;
- all declared capabilities are used/justified;
- all IDs are stable/versioned as required;
- no raw host access;
- no secret access;
- no private scheduler;
- no cross-Skill private imports;
- health is read-only;
- disable retains state by default;
- tests require no production access;
- docs match implementation.
