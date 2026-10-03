# Skill Scheduler

The Skill Scheduler is a shared Runtime service. It is not owned by Recurring Posts or any other individual Skill.

## Purpose

Skills may schedule future work through a portable SDK contract while the host owns persistence, execution leases and restart recovery.

The scheduler is designed to support multiple Skills such as:

- Recurring Posts
- Events / LFG reminders
- temporary-voice follow-up tasks
- deal/feed refresh jobs
- Twitch or integration recovery
- future AI/background tasks

A Skill must not create its own competing scheduler loop when the shared scheduler fits the use case.

## Supported V1 schedules

### One-shot

Run once at a future epoch.

### Interval

Run every N seconds. The Runtime minimum is 60 seconds.

Individual Skills may impose stricter anti-spam limits. For example, Recurring Posts can require a much larger minimum interval without changing the generic scheduler.

### Daily fixed time

Example:

```text
12:00 Europe/Berlin
```

### Weekly fixed time

Example:

```text
Monday 10:00 Europe/Berlin
```

Fixed-time schedules carry an explicit IANA timezone.

## DST behavior

For fixed local times:

- a non-existent wall time during the spring DST transition is skipped;
- an ambiguous wall time during the autumn transition uses the first occurrence;
- the same local schedule is therefore not intentionally executed twice.

## Stable job identity

Every job is scoped by:

```text
guild_id + skill_id + job_key
```

Calling upsert again for the same identity updates that job rather than creating a duplicate.

Handlers also have stable versioned IDs, for example:

```text
recurring-post.execute.v1
```

A Skill registers its handler before scheduling jobs that reference it.

## Persistence

The portable Runtime depends on `SchedulerStorePort`.

GamerHQ implements this through the additive `skill_jobs` SQLite table.

The portable scheduler does not know SQLite exists.

Stored state includes:

- owning guild and Skill
- job key
- handler ID
- schedule
- payload
- next run
- last successful run
- failure count
- bounded last error code
- temporary execution lease

## Restart behavior

Jobs are persisted before execution.

After a process restart, due jobs remain available from the host store. The scheduler does not reconstruct jobs from Discord messages or in-memory state.

## Duplicate execution protection

The GamerHQ store atomically claims due jobs with a short lease.

This protects against:

- overlapping scheduler ticks;
- crash/restart windows;
- accidental concurrent scheduler work.

GamerHQ still retains the existing production invariant of one active bot process per live guild.

## Disabled Skills

A disabled Skill never executes scheduled work.

Recurring jobs advance to their next normal occurrence while disabled rather than creating catch-up spam.

A due one-shot job is retained with a short retry window so it is not silently lost merely because the Skill was disabled when it became due.

## Failure handling

One job failure must not crash the scheduler worker.

The scheduler stores a bounded error **type/code**, not raw exception messages or private payload values.

Malformed persisted configuration is disabled for owner diagnostics rather than repeatedly crashing the worker.

## Skill-facing API

A Skill receives a scoped scheduler through `SkillContext`.

Conceptually:

```python
await ctx.scheduler.upsert_job(
    key="post:welcome",
    handler_id="recurring-post.execute.v1",
    schedule={
        "type": "daily",
        "hour": 12,
        "minute": 0,
        "timezone": "Europe/Berlin",
    },
    payload={"postId": "welcome"},
)
```

And removal:

```python
await ctx.scheduler.remove_job(key="post:welcome")
```

The scoped adapter supplies the guild and Skill identity. A Skill cannot schedule a job under another Skill's namespace through this public API.

## Security / capability

A Skill must declare:

```text
scheduler.jobs
```

The Runtime rejects handler registration and job mutation when the capability is absent.

## Not included yet

This slice does not provide:

- cron expressions
- activity/message-count triggers
- a scheduler administration UI
- Recurring Posts itself
- arbitrary sub-minute job loops
- distributed multi-node scheduling

These may be added later without changing the basic Skill contract.
