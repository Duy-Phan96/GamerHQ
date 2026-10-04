# Skill Design — Recurring Posts

## Identity

- Skill ID: `recurring-posts`
- Package: `gamerhq-skill-recurring-posts`
- Version: `1.0.0`
- Runtime API: `1`

## Purpose

Schedule recurring Discord messages without a Skill-owned background scheduler.

## Capabilities

- `discord.channels.read`: validate configured channel IDs.
- `discord.messages.send`: deliver configured posts.
- `scheduler.jobs`: persist future execution.
- `storage.skill`: persist configuration/delivery state.
- `events.emit`: emit confirmed-send events.
- `audit.write`: record administrative changes.

## Storage

- `posts.v1`: versioned map of recurring post records.

## Scheduler

- Handler: `recurring-post.execute.v1`
- Job key: `post:<post-id>`

## Event

- `recurring-post.sent.v1`

## Management APIs

- list/get/create/set-active/delete v1 contracts.

## Delivery model

Known send failures may retry. A crash in the uncertain external-delivery window
favors avoiding duplicate catch-up messages rather than claiming exactly-once
Discord delivery.
