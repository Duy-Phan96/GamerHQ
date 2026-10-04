# Skill Review Checklist

Use this checklist for every first-party or external GamerHQ Skill before allowing it into a production deployment.

## Identity and packaging

- [ ] Stable lowercase kebab-case Skill ID.
- [ ] Semantic Skill version.
- [ ] Supported Runtime API version.
- [ ] External package entry point uses `gamerhq.skills`.
- [ ] Entry-point name exactly matches manifest ID.
- [ ] Factory has no installation/runtime side effects.

## Architecture

- [ ] Portable code imports only public SDK/standard-library/Skill-owned dependencies.
- [ ] No GamerHQ `database`, `services`, `cogs`, `hosts`, `bot`, or Discord.py imports.
- [ ] No imports from another Skill's private implementation.
- [ ] No duplicate scheduler/storage/host subsystem was introduced.

## Capabilities

- [ ] Every host operation maps to a declared capability.
- [ ] Requested capabilities are minimal.
- [ ] External package static capability metadata exactly matches SkillManifest.permissions.
- [ ] No capability is requested only for hypothetical future work.
- [ ] Missing capabilities fail closed.

## Lifecycle

- [ ] `register` is process-level and idempotent.
- [ ] `enable` is guild-scoped and retry-safe.
- [ ] `start` does not create an unnecessary scheduler loop.
- [ ] `stop` is safe during shutdown.
- [ ] `disable` retains configuration/data by default.
- [ ] `health_check` is read-only.

## Persistence

- [ ] Skill uses namespaced Skill Storage only.
- [ ] Stored structures are JSON serializable.
- [ ] Evolving structures have a schema/version strategy.
- [ ] Migrations are deterministic and non-destructive by default.
- [ ] No raw production DB access.

## Scheduler

- [ ] Uses shared `scheduler.jobs`.
- [ ] Stable job keys.
- [ ] Stable versioned handler IDs.
- [ ] Payload is small and contains identifiers rather than duplicated private content.
- [ ] Retry/duplicate behavior is documented.
- [ ] Concurrent edit/delete and stale execution are safe.

## Discord / host resources

- [ ] Uses host-neutral IDs/metadata.
- [ ] Does not keep Discord.py objects.
- [ ] Cross-guild access is impossible through the Skill API.
- [ ] Missing/deleted resources are handled safely.
- [ ] Mentions and privileged behavior are least-privilege.

## Cross-Skill contracts

- [ ] Events used for notifications.
- [ ] Public APIs used only for true request/response needs.
- [ ] Event/API IDs are versioned.
- [ ] Payloads are documented.
- [ ] Private content is not shared unnecessarily.

## Host management contracts

- [ ] Host configuration does not import private Skill implementation modules.
- [ ] Custom configuration uses declared versioned Management APIs.
- [ ] Management payloads are bounded and host-neutral.
- [ ] Management handlers do not expose raw host objects.

## UX

- [ ] Configuration lives under normal host management UX.
- [ ] Persistent/destructive actions have appropriate review/confirmation.
- [ ] Authorization is rechecked at mutation time.
- [ ] Messages are understandable and avoid internal IDs/errors.

## Security

- [ ] No token/.env access.
- [ ] No arbitrary shell execution.
- [ ] No runtime package installation or Git clone.
- [ ] No raw private exception text exposed/persisted.
- [ ] No operation can accidentally make private server resources public.

## Tests

- [ ] Tests run without Discord token.
- [ ] Manifest/contract tests.
- [ ] Lifecycle/idempotency tests.
- [ ] Capability denial tests.
- [ ] Storage tests.
- [ ] Scheduler tests where applicable.
- [ ] Missing-resource/failure tests.
- [ ] Retry/deduplication tests for visible side effects.
- [ ] Configuration authorization tests.

## Documentation

- [ ] README explains purpose and configuration.
- [ ] Capabilities documented with rationale.
- [ ] Storage schema documented.
- [ ] Scheduler jobs/handlers documented.
- [ ] Events/APIs documented.
- [ ] Upgrade/migration behavior documented.
- [ ] Security/privacy behavior documented.

## Release decision

- [ ] All required CI checks pass, excluding explicitly documented repository-wide unrelated gates.
- [ ] Package/commit version is pinned for deployment.
- [ ] No production deployment is performed as part of code review.
- [ ] Live acceptance steps are documented separately.
