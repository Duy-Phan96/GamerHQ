# GamerHQ Skill Developer Guide

This is the canonical guide for developers building portable GamerHQ Skills.

A Skill is an independently versioned feature package that runs through the GamerHQ Skill Runtime. It must depend only on public SDK contracts and declared host capabilities.

## 1. Repository layout

Recommended external repository structure:

```text
gamerhq-skill-example/
├─ pyproject.toml
├─ README.md
├─ CHANGELOG.md
├─ LICENSE
├─ gamerhq_skill_example/
│  ├─ __init__.py
│  ├─ skill.py
│  ├─ models.py
│  └─ ...
└─ tests/
   ├─ test_manifest.py
   ├─ test_lifecycle.py
   └─ ...
```

Keep the public entry point small. Private implementation files remain internal to the Skill package.

## 2. Stable identity

Every Skill has one permanent machine ID:

```python
SkillManifest(
    id="example-skill",
    ...
)
```

Rules:

- lowercase kebab-case;
- never derive identity from the display name;
- do not rename an existing ID after release;
- the Python entry-point name must exactly match the manifest ID.

The package name may be `gamerhq-skill-example`, while the runtime Skill ID remains `example-skill`.

## 3. Package entry point

External Skills use the standard Python entry-point group:

```toml
[project.entry-points."gamerhq.skills"]
example-skill = "gamerhq_skill_example:create_skill"
```

The target must be a zero-argument callable returning one Skill instance.

```python
def create_skill():
    return ExampleSkill()
```

Do not perform network calls, database migrations, Discord writes or other side effects inside the factory.

## 4. Manifest is the public dependency declaration

The manifest must describe what the Skill needs before it is enabled.

Declare only required capabilities.

Example:

```python
manifest = SkillManifest(
    id="example-skill",
    name="Example Skill",
    version="1.0.0",
    runtime_api_version="1",
    description="Example portable GamerHQ Skill.",
    author="Example Developer",
    permissions=(
        SkillCapability.STORAGE_SKILL.value,
        SkillCapability.SCHEDULER_JOBS.value,
    ),
)
```

Do not request broad permissions for possible future features. Add a capability when a released feature actually needs it.

For external packages, mirror the exact capability list in `[tool.gamerhq].capabilities` so reviewers can inspect permissions without executing the package. The package preflight requires that list to match `SkillManifest.permissions` exactly.

## 5. Capability model

Capabilities are the only supported Skill → host privilege boundary.

Examples:

- `discord.channels.read`
- `discord.messages.send`
- `discord.embeds.send`
- `scheduler.jobs`
- `storage.skill`
- `events.emit`
- `events.subscribe`
- `skills.api.call`
- `audit.write`

A capability must be:

1. known by the Runtime;
2. declared in the Skill manifest;
3. available from the current host.

All three conditions are required.

Never import Discord.py, GamerHQ database helpers, host services or the bot object to bypass a capability boundary.

## 6. Lifecycle

Every Skill implements:

```text
register(registration_ctx)
enable(ctx)
disable(ctx)
start(ctx)
stop(ctx)
health_check(ctx)
```

### register

Runs once per process.

Use it only to bind process-stable contracts such as:

- scheduler handler IDs;
- Public Skill API handlers.

Do not read guild configuration here.

Do not start private background loops here.

Do not capture a Discord client, Guild object, raw DB connection or guild-specific state.

### enable

Runs when the Skill becomes enabled for one guild.

Use it for lightweight, idempotent enable-time validation or initialization through public SkillContext ports.

Do not create duplicate resources on repeated calls.

### start

Runs for an enabled guild when its runtime lifecycle starts.

Use it for guild-scoped event subscriptions or other runtime bindings that cannot be process-global.

Do not use `start` to create a competing scheduler loop for persisted scheduled work.

### stop

Stops guild-scoped runtime work.

It must be safe during shutdown and disable.

### disable

Runs when the Skill is disabled for a guild.

Disable is not uninstall.

Do not delete user configuration, historical data or persisted scheduler state unless that destructive behavior is an explicit separately confirmed user action.

### health_check

Must be read-only.

Return a bounded, understandable health result. Never repair state during a health check.

## 7. SkillContext

All guild-scoped host access comes through `SkillContext`.

Typical usage:

```python
await ctx.storage.set("config", data)
await ctx.scheduler.upsert_job(...)
await ctx.discord.send_message(...)
await ctx.events.emit(...)
await ctx.audit.write(...)
```

The context is already scoped to:

```text
guild_id + skill_id
```

A Skill must not attempt to supply another guild or Skill identity to bypass this scope.

## 8. Storage

Use `storage.skill` for private persistent Skill data.

Rules:

- store JSON-serializable values only;
- use stable storage keys;
- treat storage as private to the Skill;
- do not read another Skill's storage;
- do not use raw GamerHQ tables;
- include explicit data-version fields when stored structures may evolve.

Recommended pattern:

```json
{
  "schemaVersion": 1,
  "items": []
}
```

Migration logic must be deterministic and backward compatible where practical.

## 9. Scheduler

Use `scheduler.jobs` for persisted future work.

Do not create a private asyncio loop for ordinary recurring jobs.

Each scheduled operation must have:

- stable job key;
- stable versioned handler ID;
- small JSON payload;
- supported schedule contract.

Example handler ID:

```text
example-skill.cleanup.v1
```

Example job key:

```text
cleanup:daily
```

The scheduler provides persistence, restart recovery, leases and stale-worker protection.

It does not guarantee exactly-once external side effects. A Skill must design its externally visible actions with retry/idempotency behavior appropriate to the operation.

## 10. Discord

Portable Skills never receive Discord.py objects.

Use host-neutral IDs and returned SDK metadata.

Default assumptions:

- mentions should be disabled unless explicitly needed;
- validate channel availability before saving configuration;
- handle host-neutral missing-resource and permission errors;
- never persist raw Discord.py exception text.

Do not guess resources by name when an ID is required for ownership or identity.

## 11. Events

Use Events for:

> Something happened.

Event IDs are versioned compatibility contracts.

Example:

```text
example-skill.completed.v1
```

An Event payload should be:

- small;
- JSON-like;
- documented;
- free of unnecessary private content;
- stable for the event version.

Do not include message bodies, secrets or large private records unless consumers genuinely require them and the privacy contract explicitly allows it.

## 12. Public Skill APIs

Use a Public Skill API only for direct request/response behavior.

Prefer Events when the producer does not need a response.

A Skill must never import another Skill's private Python implementation.

Cross-Skill communication is limited to:

1. host capabilities;
2. documented Events;
3. documented Public Skill APIs.

## 13. Host management APIs

Use a versioned Management API when the trusted host administration UI needs
to configure or inspect a Skill without importing its private implementation.

Management APIs are **host → Skill** contracts.

They are different from Public Skill APIs:

- Management API: trusted host administration/configuration.
- Public Skill API: Skill → Skill request/response.

A portable Skill may declare management operations such as:

```text
my-skill.list.v1
my-skill.get.v1
my-skill.create.v1
my-skill.update.v1
my-skill.delete.v1
```

Register handlers only through `SkillRegistrationContext.management`.

The host must never import private Skill classes/models merely to render a
configuration screen.

Management request/response payloads must be small, versioned, documented and
host-neutral.

## 14. Configuration UX

A Skill should expose configuration through the host's normal management surface, not invent an unrelated admin system.

For GamerHQ:

```text
/server manage → Skills → <Skill> → Configure
```

Configuration changes should follow:

```text
inspect current state
→ collect input
→ preview/review when material
→ confirm destructive/persistent changes
→ apply idempotently
→ show result
```

Owner-only/destructive actions must recheck authorization at confirmation time.

## 15. Security requirements

A Skill must never:

- read `.env` or bot secrets;
- access a raw Discord token;
- import the GamerHQ bot/client;
- open GamerHQ's database directly;
- bypass capability checks;
- execute arbitrary shell commands;
- install packages at runtime;
- clone arbitrary repositories at runtime;
- write outside its documented storage/capability surfaces;
- expose private exception details to users or persisted generic state;
- silently make private Discord resources public.

External HTTP access, roles, voice management and other privileged behavior require corresponding host capabilities before use.

## 16. Error handling

Use safe SDK/host-neutral exceptions where available.

Separate:

- invalid user/configuration input;
- unavailable capability;
- missing host resource;
- permission denial;
- transient host failure;
- internal Skill bug.

One failed scheduled job or event consumer must not intentionally crash unrelated Skills.

Never put secrets, message content or arbitrary exception strings into generic error codes.

## 17. Versioning

Use semantic versioning for the Skill.

Examples:

- `1.0.1`: compatible bug fix;
- `1.1.0`: backward-compatible feature;
- `2.0.0`: breaking Skill behavior/config contract.

Runtime API compatibility is separate:

```python
runtime_api_version="1"
```

Do not increase the Runtime API version just because the Skill version changed.

Events, Public APIs and scheduler handler IDs are separately versioned contracts.

## 18. Backward compatibility

Released Skills should assume existing users may have:

- older stored configuration;
- persisted jobs;
- disabled-but-retained state;
- resources that were deleted manually;
- a newer or older compatible Skill version after restart.

Never require deleting production data simply to upgrade a normal Skill version.

## 19. Testing requirements

A Skill repository should run offline without a Discord token.

Minimum coverage:

- manifest validation;
- lifecycle idempotence;
- storage roundtrip/migrations;
- capability-denial behavior;
- scheduler registration;
- job creation/update/delete;
- restart-compatible persisted state;
- invalid configuration;
- missing resources;
- authorization for configuration flows;
- retry/idempotency behavior for external side effects.

Use fakes for SDK ports.

Live Discord access is acceptance testing, not the normal unit-test environment.

External repositories should also use the [External Skill CI](external-ci.md) pattern to run the same offline checks on Python 3.12 and 3.14. Declare and validate the [Package Compatibility](package-compatibility.md) metadata so package identity, Runtime API and SDK range stay explicit.

## 20. Documentation requirements

Every Skill repository should document:

- Skill ID;
- package name;
- Skill version;
- Runtime API version;
- purpose;
- required capabilities;
- configuration;
- storage keys/schema;
- scheduler handlers/jobs;
- emitted Events;
- consumed Events;
- exposed Public APIs;
- consumed Public APIs;
- security/privacy behavior;
- failure/retry behavior;
- upgrade/migration notes;
- installation instructions;
- test command.

## 21. Definition of Done

A Skill is ready for review only when:

- its manifest is valid;
- package entry point matches manifest ID;
- requested capabilities are minimal;
- no host/private imports exist;
- lifecycle is idempotent;
- storage is namespaced/versioned where needed;
- scheduled work uses the shared scheduler;
- contracts are versioned;
- configuration uses normal host UX;
- destructive changes require confirmation;
- health is read-only;
- offline tests pass;
- README documents all dependency surfaces;
- no secrets or production data are required for tests.

Run the [SDK Conformance Check](conformance.md), including the static source audit, as a fast offline preflight, then use the [Skill Review Checklist](review-checklist.md) for full review.
