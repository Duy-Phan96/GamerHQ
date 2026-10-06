# Skill Runtime / SDK

GamerHQ is the **first host** of the Skill Runtime. It is not the definition of the Runtime.
The portable runtime and public SDK contracts must remain free of GamerHQ-specific business logic so they can later move into an independent package or repository.

## Portability Rule

A public Skill contract must not require GamerHQ internals. Skills must not import another Skill's private implementation, GamerHQ database helpers, GamerHQ services, or a raw Discord client as part of the public programming model.

Portable Skills communicate through five mechanisms:

| Mechanism | Direction | Use it for |
| --- | --- | --- |
| Capability | Skill → host | Send/edit messages, schedule work, use private storage, write audit records |
| Event | Skill → subscribers | Announce that something happened without knowing who consumes it |
| Public Skill API | Skill → Skill | Explicit request/response operations that cannot be modeled as notifications |
| Skill Storage | Skill → its namespace | Private persistent state owned by one Skill |
| Scheduler | Skill → host | Future execution owned by a Skill |

## SDK Rule

Every capability, event, and public Skill API that another Skill may depend on must be:

- identified by a stable machine-readable ID;
- versioned where its payload/behavior is a compatibility contract;
- declared in the Skill manifest;
- documented;
- testable without access to the producing Skill's internals.

## Events vs Public Skill APIs

Prefer an **Event** when the meaning is "something happened":

`recurring-post.sent.v1`

Use a **Public Skill API** only for a direct request/response need:

`events.get-event.v1`

A consumer must never do this:

```python
from skills.events.internal_service import get_event
```

Instead it uses the Runtime contract:

```python
result = await ctx.skills.call(
    skill_id="events",
    contract_id="events.get-event.v1",
    payload={"eventId": "123"},
)
```

The Runtime/host can later route the same contract in-process, to another process, or to a remote service without changing the consuming Skill.

## Skill lifecycle

Every Skill implements:

1. `register(registration_ctx)`
2. `enable(ctx)`
3. `disable(ctx)`
4. `start(ctx)`
5. `stop(ctx)`
6. `health_check(ctx)`

Per-guild enablement is host/runtime state. A disabled Skill must not continue scheduled jobs for that guild.

## Manifest

The manifest is the install-time contract. Example conceptual declaration:

```python
SkillManifest(
    id="recurring-posts",
    name="Recurring Posts",
    version="1.0.0",
    runtime_api_version="1",
    description="Automatically posts configured messages.",
    author="GamerHQ",
    permissions=(
        "discord.messages.send",
        "scheduler.jobs",
        "storage.skill",
        "events.emit",
    ),
    events=SkillEvents(
        emits=(EventContract("recurring-post.sent.v1"),),
    ),
)
```

Display names may change. Machine IDs are compatibility contracts and should not.

## Extraction Rule

The portable `skill_runtime/` package must not import GamerHQ-specific modules. GamerHQ integrations belong in a host-adapter layer. This keeps future extraction practical instead of requiring a rewrite.

## Registry and Manager boundary

The portable `SkillRegistry` validates code-level registrations. It does **not** decide which guild has enabled a Skill.

`SkillManager` coordinates lifecycle per guild using a host-provided persistent state port:

- duplicate registration fails;
- unsupported Runtime API versions fail before activation;
- enable/disable is idempotent per guild;
- disabled Skills cannot start;
- restart restoration reads persisted enabled Skill IDs and starts only registered compatible Skills;
- an unknown persisted Skill ID fails closed and should be surfaced by host health diagnostics.

The manager does not know whether the host uses SQLite, PostgreSQL, a service, or another persistence mechanism.


## External packages

A Skill may live in its own Git repository and be installed as a Python package.
External packages use the `gamerhq.skills` entry-point group and are loaded only
when their stable Skill ID appears in the deployment allowlist.

See [External Skill packages](external-packages.md).


## Authoring

Start here when building a new Skill:

1. [Skill Developer Guide](developer-guide.md)
2. [Strict Authoring Contract](authoring-contract.md)
3. [SDK Conformance Check](conformance.md)
4. [Skill Review Checklist](review-checklist.md)
5. [External Skill packages](external-packages.md)
6. [External Skill CI](external-ci.md)
7. [Package Compatibility](package-compatibility.md)
8. [Extracting a Skill](extracting-a-skill.md)
9. [Creating a Skill](creating-a-skill.md)
10. [Web Platform & Skill Marketplace](web-platform-marketplace.md)

The Developer Guide is the primary human-facing reference. The Authoring
Contract is intentionally stricter and may also be used as input for code
generation or AI-assisted Skill creation.
