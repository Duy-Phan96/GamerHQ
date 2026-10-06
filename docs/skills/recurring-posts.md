# Recurring Posts Skill

Recurring Posts is GamerHQ's first complete reference external Skill package.

Its purpose is both practical and architectural: it proves that a real feature
can be implemented, packaged and discovered independently without importing
GamerHQ business logic, Discord.py or raw database helpers.

The package now lives in the independent repository:

`Duy-Phan96/gamerhq-skill-recurring-posts`

GamerHQ installs a reviewed immutable commit through
`requirements-skills.lock`. The Skill ID, storage namespace and management
contracts remain stable across repository extraction.

## User flow

Open:

```text
/server manage
  → Skills
  → Recurring Posts
  → Enable
  → Configure
```

The owner can:

- choose a destination text/announcement channel;
- create interval, daily or weekly schedules;
- use IANA timezones for fixed local times;
- preview a configuration before saving;
- edit name, destination channel, message and schedule;
- pause and resume a post;
- preview deletion impact before confirming removal;
- delete a configuration and its scheduler job.

The minimum interval is 15 minutes. The host shows that limit directly in the
interval field and keeps the Skill's server-side validation authoritative.

## Platform dependencies

Manifest capabilities:

- `discord.channels.read`
- `discord.messages.send`
- `scheduler.jobs`
- `storage.skill`
- `events.emit`
- `audit.write`

Configuration is stored in the Skill's private `storage.skill` namespace.

Scheduled work is stored only in the shared host `skill_jobs` table through
`scheduler.jobs`.

No Recurring Posts database table or private scheduler exists.

## Registration and execution

At process registration:

```text
RecurringPostsSkill.register(...)
        ↓
recurring-post.execute.v1
        ↓
shared SchedulerEngine
```

When a persisted job executes, the host supplies a new guild-scoped
`SkillContext`. The handler therefore sees only the guild, storage and
capabilities it is allowed to use.

## Disabled behavior

Disabling the Skill does not destroy its configuration.

The shared Scheduler's existing disabled-Skill gate prevents job execution while
disabled. Persisted state remains available for re-enable.

## Delivery safety

The scheduler protects claims with leases, tokens and revisions, but Discord is
an external side effect and cannot be made exactly-once by the scheduler alone.

Recurring Posts stores the scheduler slot before delivery and the confirmed
Discord message ID after delivery.

- known send failure: clear the pending reservation so retry is allowed;
- repeated execution of the same completed slot: do not send again;
- process death in the uncertain delivery window: favor avoiding a duplicate
  catch-up message rather than claiming an exactly-once guarantee that does not
  exist.

## Event

After a confirmed send, the Skill emits:

`recurring-post.sent.v1`

The payload contains identifiers and timing metadata, not a duplicate copy of
the message body.

## Limits

Current V1:

- maximum 20 configured posts per guild;
- message content follows Discord's 2000-character limit;
- interval schedules must be at least 15 minutes;
- interval, daily and weekly schedules only;
- no arbitrary cron expressions.

These constraints keep the first reference Skill simple, predictable and safe.


## Package boundary

Distribution: `gamerhq-skill-recurring-posts`

Entry point:

```toml
[project.entry-points."gamerhq.skills"]
recurring-posts = "gamerhq_skill_recurring_posts:create_skill"
```

GamerHQ installs the package in the reviewed production image and loads it
through the same external package discovery path used by third-party Skills.

The host UI does not import the package implementation. Configuration is routed
only through the versioned Recurring Posts Management APIs.

Version 1.2 adds Management UI Schema V1 on top of the existing public
management contracts.

Recurring Posts now declares a generic collection schema with list/create/get,
validate, update, pause/resume, delete-preview and delete operations plus
host-neutral item fields for name, Discord channel, message, schedule and active
state.

This allows Discord administration and gamerhq-web to build the management
surface without importing private Skill classes or creating a Recurring
Posts-specific frontend page.
