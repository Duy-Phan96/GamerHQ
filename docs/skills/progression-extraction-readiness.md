# Progression extraction readiness

This document freezes the built-in Progression Skill boundary before moving its
portable implementation into a standalone repository.

It is a readiness gate, not the extraction itself.

## Decision

The portable Skill may be extracted once this readiness branch passes the normal
GamerHQ CI.

The later extraction must preserve the contracts below exactly unless a separate
versioned migration is intentionally reviewed.

## Stable identity

| Surface | Frozen value |
| --- | --- |
| Skill ID | `progression` |
| Skill version | `0.1.0` |
| Runtime API | `1` |
| Factory | `create_skill()` |

The extraction must not rename the Skill. GamerHQ Skill Storage is scoped by
guild + Skill ID, so changing `progression` would create a new namespace.

## Stable storage namespace

Progression currently owns these Skill Storage keys:

```text
config.v1
member.v1:<discord-member-id>
```

No GamerHQ database table belongs to Progression.

### Configuration compatibility

`config.v1` is the current configuration schema key.

The embedded `revision` field is an optimistic-concurrency revision used by
Management UI writes. It is not a replacement for the storage key's schema
version.

The extraction must keep `config.v1` so existing guild configuration remains
available without migration.

### Member-state compatibility

`member.v1:<id>` is the current member-state schema key.

Reads are additive: missing fields are normalized in memory to safe defaults.
A read-only status/health operation does not rewrite old state.

Future incompatible member-state changes must use an explicit migration or a new
versioned storage strategy. Extraction alone is not such a change.

## Frozen Management API IDs

The host currently integrates only through these versioned contracts:

```text
progression.status.v1
progression.get-config.v1
progression.update-config.v1
progression.preview-level.v1
progression.record-activity.v1
progression.member-status.v1
```

GamerHQ host adapters must continue to call these contracts after extraction.
They must not import private package implementation.

## Capability contract

The standalone package metadata must exactly match the manifest capabilities:

```text
storage.skill
audit.write
discord.messages.send
discord.channels.read
discord.members.read
discord.roles.manage
```

Do not add capabilities during extraction unless separately required and
reviewed.

Progression does not currently request `scheduler.jobs`.

## Scheduler model

Progression owns no scheduler job today.

GamerHQ host adapters observe Discord/server activity and submit bounded,
deduplicated activity records through `progression.record-activity.v1`.

That boundary remains after extraction.

Do not move GamerHQ event/voice observation into the portable package merely to
make the package appear more self-contained.

## What moves to the standalone repository

The future `gamerhq-skill-progression` repository should own:

- the portable Progression implementation currently in `skills/progression.py`;
- Skill manifest and factory;
- XP/level math;
- configuration validation;
- achievement/reward logic;
- Skill-owned member/config storage behavior;
- Management APIs and Management UI Schema;
- standalone unit/conformance tests;
- README, SKILL_DESIGN, CHANGELOG and RELEASE_CHECKLIST;
- package metadata and the `gamerhq.skills` entry point.

## What stays in GamerHQ

GamerHQ continues to own:

- `cogs/progression_activity.py`;
- Discord voice/activity observation;
- completed-event observation;
- Host Runtime/SDK/capabilities;
- Discord resource adapters;
- external package loading and provenance;
- reviewed immutable package pinning.

The host adapter must continue to communicate only through public Runtime /
Management contracts.

## Automated readiness gates

GamerHQ tests now verify:

- the portable source passes `require_clean_skill_source`;
- `create_skill` passes public SDK factory/lifecycle conformance;
- the stable Skill ID/version/Runtime API are unchanged;
- Management API IDs are unchanged;
- manifest capabilities match the frozen capability tuple;
- storage key names are unchanged;
- partial legacy member state remains readable without mutation;
- health and disable are non-destructive.

## Extraction sequence after this gate

Once this readiness PR is green:

1. create `gamerhq-skill-progression` from the external Skill template;
2. copy only portable Skill-owned code/tests;
3. add package metadata and `gamerhq.skills` entry point;
4. run Python 3.12/3.14 package CI, source audit and conformance;
5. review one immutable external commit/release;
6. add that immutable package pin to GamerHQ;
7. prove external discovery/Management routing using the same Skill ID;
8. remove the built-in Progression implementation/registration from GamerHQ;
9. keep `cogs/progression_activity.py` as the host adapter;
10. run full GamerHQ CI before any production deployment.

Only one implementation of Skill ID `progression` may be registered at a time.

## Database and deployment impact

This readiness work requires no database migration and no deployment.

The later extraction should also require no data migration if the Skill ID and
storage keys above remain unchanged. Production rollout still requires a normal
reviewed image build, database backup and live acceptance.
