# Creating a Skill

This document describes the future-facing SDK contract. Recurring Posts will become the first reference implementation.

## 1. Create a Skill package

A future Skill should contain a manifest, implementation, tests, and documentation. Internal files are private unless a contract explicitly exposes them.

## 2. Choose a stable Skill ID

Use lowercase kebab-case, for example `recurring-posts`. The display name may change; the ID should not.

## 3. Declare capabilities

Request only what the Skill needs. A Skill that does not declare `discord.roles.manage` must not receive that capability from its host context.

The first Discord host capabilities are intentionally narrow:

- `discord.channels.read` — resolve minimal metadata for a channel in the current guild;
- `discord.messages.send` — send into a channel in the current guild;
- `discord.embeds.send` — allow an embed payload in addition to message sending.

Declaring a known capability is not enough by itself. The active host must also
provide it. Activation fails closed when a required capability is unavailable.
Portable Skills never receive the Discord client, a Guild object, or a raw
Channel object.

## 4. Define events

Events are versioned notifications. Document producer, consumers, payload fields, emission guarantees, privacy considerations, and failure semantics.

Example:

- ID: `recurring-post.sent.v1`
- Producer: Recurring Posts
- Meaning: Discord confirmed the scheduled send
- Payload: `guildId`, `postId`, `channelId`, `discordMessageId`, `timestamp`
- Content body: not included by default

## 5. Define Public Skill APIs only when needed

Do not create an API for every Skill by default. Prefer Events for loose coupling. Add a Public Skill API only when another Skill needs direct request/response behavior.

Every public contract must have a versioned ID such as `events.get-event.v1` and be declared in the manifest.

## 6. Register process-level handlers

`register(ctx)` receives a `SkillRegistrationContext`. Use it only for
process-stable bindings such as versioned scheduler handlers or Public Skill API
handlers.

A registered scheduler handler receives a fresh guild-scoped `SkillContext`
when it actually executes.

Do not keep a Guild object, Discord client or database connection in registration
state.

## 7. Use SkillContext


Use host capabilities such as:

```python
await ctx.discord.send_message(...)
await ctx.scheduler.upsert_job(...)
await ctx.storage.set(...)
await ctx.events.emit(...)
await ctx.audit.write(...)
```

Do not use the Discord token, raw GamerHQ DB access, another Skill's private tables, or another Skill's internal Python modules.

## 8. Keep storage private

The host gives each Skill a namespaced storage view. Shared information crosses boundaries through documented Events or Public Skill APIs.

## 9. Test contracts without production

SDK tests should validate manifests, capabilities, event schemas, API IDs, lifecycle compatibility, storage isolation, and later scheduler behavior using fakes. A third-party developer must not need a live Discord token to run contract tests.

## 10. Document every dependency surface

Each Skill README should list:

- Skill ID and version
- Runtime API version
- capabilities required
- events emitted
- events consumed
- Public APIs exposed
- Public APIs consumed
- storage namespace
- scheduler jobs
- configuration
- health checks
- failure behavior
- security notes
- examples

## 11. Forbidden coupling

A Skill must not import another Skill's private implementation. Allowed collaboration mechanisms are:

1. Core/host capabilities
2. documented Events
3. explicit versioned Public Skill APIs


## Scheduler jobs

If a Skill needs future execution, declare `scheduler.jobs` and use the shared scheduler through `SkillContext`.

Do not start a private recurring loop for ordinary persisted schedules.

Each job should use:

- a stable job key inside the Skill namespace;
- a stable versioned handler ID;
- one of the supported schedule contracts;
- a small JSON payload containing only the data needed to find the Skill-owned record.

See [Skill Scheduler](scheduler.md).


## Discord error model

Host-specific Discord.py exceptions do not cross the SDK boundary. Skills may
handle stable host-neutral failures such as unavailable capabilities, missing
resources, permission denial, invalid operations and transient host failures.

Do not inspect or persist raw Discord exception text as Skill state.

## Guild scope

Discord access is bound to the `SkillContext.guild_id`. A channel identifier
from another guild must resolve as unavailable rather than allowing cross-guild
access. Skills must not accept or pass raw Discord Guild objects through their
portable APIs.
