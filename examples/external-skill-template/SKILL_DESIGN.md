# Skill Design

Fill this out before implementing the Skill.

## Identity

- Skill ID:
- Display name:
- Package name:
- Skill version:
- Runtime API version:
- Author:

## Purpose

What problem does this Skill solve?

What is explicitly out of scope?

## Capabilities

List every required capability and why it is necessary.

| Capability | Why required |
| --- | --- |
| | |

Do not include capabilities for hypothetical future features.

## Configuration

Where does the user configure the Skill?

Expected GamerHQ path:

```text
/server manage → Skills → <Skill> → Configure
```

Describe create/edit/delete/enable/disable flows.

Which actions require owner confirmation?

## Storage

Storage keys:

| Key | Schema version | Purpose |
| --- | --- | --- |
| | | |

Describe migration behavior for older stored data.

## Scheduler

| Job key pattern | Handler ID | Schedule type | Payload |
| --- | --- | --- | --- |
| | | | |

Describe retry and duplicate-delivery behavior.

If no scheduler is needed, state that explicitly.

## Events emitted

| Event ID | Meaning | Payload | Privacy notes |
| --- | --- | --- | --- |
| | | | |

## Events consumed

| Event ID | Why consumed |
| --- | --- |
| | |

## Public APIs exposed

| Contract ID | Request | Response | Why direct request/response is needed |
| --- | --- | --- | --- |
| | | | |

## Public APIs consumed

| Provider Skill | Contract ID | Why required |
| --- | --- | --- |
| | | |

## Discord / host resources

Which resource IDs does the Skill store?

How are missing/deleted resources handled?

What mention behavior is required?

## Lifecycle

### register

What process-level handlers are registered?

### enable

What validation/initialization runs?

### start

What guild-scoped runtime work starts?

### stop

What is released?

### disable

What remains persisted?

### health_check

What read-only checks are reported?

## Failure model

For each externally visible operation describe:

- invalid configuration;
- permission denial;
- resource missing;
- transient failure;
- process crash;
- retry behavior;
- duplicate side-effect behavior.

## Security and privacy

What user/server data is stored?

What data crosses Skill boundaries?

What data is intentionally not persisted?

Why are the requested capabilities least-privilege?

## Upgrade compatibility

How will existing configuration/jobs survive a new Skill version?

What would require a major version?

## Test plan

Minimum offline tests:

- [ ] Manifest validation
- [ ] Lifecycle idempotence
- [ ] Capability denial
- [ ] Storage roundtrip/migration
- [ ] Scheduler behavior if applicable
- [ ] Missing resource handling
- [ ] Failure/retry behavior
- [ ] Duplicate-side-effect behavior
- [ ] Configuration authorization
- [ ] Health is read-only

## Acceptance plan

List optional live Discord checks after reviewed deployment.

Do not put production secrets, IDs or private data in this document.
