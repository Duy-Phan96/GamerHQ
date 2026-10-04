# Events and Public Skill APIs

Skills must collaborate without importing each other's private implementation.

There are two Skill-to-Skill mechanisms.

## 1. Events

Use an Event when the meaning is:

> Something happened.

Examples:

```text
recurring-post.created.v1
recurring-post.sent.v1
event.created.v1
member.level-up.v1
```

An event producer does not know or depend on its consumers.

A consumer subscribes through the Runtime Event Bus rather than importing the producer.

Every documented event must define:

- stable versioned event ID
- producer
- meaning
- payload schema
- when it is emitted
- delivery/error guarantees
- privacy rules
- whether payload fields contain user content
- compatibility/versioning notes

### Example

```text
ID:
recurring-post.sent.v1

Producer:
Recurring Posts

Meaning:
Discord confirmed a recurring-post execution.

Payload:
guildId
postId
channelId
discordMessageId
timestamp

Privacy:
Message content is not included by default.

Guarantee:
Emitted only after the host confirms the Discord send.
```

A future Analytics Skill can consume this event without depending on Recurring Posts internals.

## 2. Public Skill APIs

Use a Public Skill API only when another Skill needs a direct request/response operation.

Examples:

```text
events.get-event.v1
recurring-post.get-config.v1
tournaments.get-status.v1
```

Not every Skill needs a Public API.

Prefer Events for loose coupling.

### Forbidden

```python
from skills.events.internal_service import get_event
```

### Allowed

```python
result = await ctx.skills.call(
    skill_id="events",
    contract_id="events.get-event.v1",
    payload={"eventId": "123"},
)
```

The Runtime can later route that contract:

- inside the same Python process
- to another process
- to another service

without changing the consuming Skill.

## Contract ownership

The exposing Skill owns the Public API contract.

The producing Skill owns an Event contract.

Consumers may rely only on documented, versioned behavior.

Private functions, database layouts, implementation classes, and internal service names are never contracts.

## Version changes

Do not silently change an incompatible payload.

Instead introduce a new contract version:

```text
recurring-post.sent.v1
recurring-post.sent.v2
```

The Runtime may support multiple versions during a migration period.

## SDK documentation rule

Every Skill README must contain:

```text
Events emitted
Events consumed
Public APIs exposed
Public APIs consumed
```

If a section is empty, state `None`.

This makes dependency surfaces reviewable before installation and allows automated SDK validation later.
