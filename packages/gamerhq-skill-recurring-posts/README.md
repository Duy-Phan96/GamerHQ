# GamerHQ Skill — Recurring Posts

Reference external Skill package for the GamerHQ Skill Runtime.

Skill ID: `recurring-posts`  
Version: `1.0.0`  
Runtime API: `1`

## Purpose

Create persistent interval, daily or weekly Discord posts through host
capabilities and the shared Skill Scheduler.

## Capabilities

- `discord.channels.read`
- `discord.messages.send`
- `scheduler.jobs`
- `storage.skill`
- `events.emit`
- `audit.write`

## Management APIs

- `recurring-posts.list.v1`
- `recurring-posts.get.v1`
- `recurring-posts.create.v1`
- `recurring-posts.set-active.v1`
- `recurring-posts.delete.v1`

The host UI must use these management contracts. It must not import this
package's private models or implementation.

## Persistence

Storage key: `posts.v1`

Scheduler handler: `recurring-post.execute.v1`

Moving this package to a separate Git repository does not change its Skill ID,
storage namespace or scheduler identity.

## Development

This package is intentionally laid out exactly like a future standalone GitHub
repository. During the transition it lives under GamerHQ's `packages/`
directory so integration and deployment can be tested before the repository
move.

Run the GamerHQ SDK conformance/source-audit checks and the package's offline
tests before release.
