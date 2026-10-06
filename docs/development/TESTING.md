# Testing Strategy

GamerHQ testing is designed to keep AI-assisted changes safe without requiring a
live Discord server for normal development.

## Principles

- Prefer automated checks over instructions people must remember.
- Test the smallest owning layer first.
- Add regression tests for fixed bugs.
- Keep tests offline and synthetic by default.
- Use live Discord/VPS acceptance only for behavior that cannot be proven offline.
- Never use real production secrets or private community content in tests.
- External Skills must be testable independently from GamerHQ application internals.

## Test levels

### Unit tests

Use for deterministic business rules and pure helpers.

Examples:

- validation;
- level calculations;
- schedule parsing;
- capability checks;
- configuration transformations.

### Integration tests

Use when multiple real project components interact.

Examples:

- SQLite adapters + host lifecycle;
- Runtime + registry + management router;
- Host Web API + authoritative service;
- migrations against legacy database shapes;
- package discovery.

Use temporary databases and mocked/synthetic Discord objects.

### Contract tests

Critical for the platform/Skill ecosystem.

Cover:

- Skill manifests;
- Runtime API version;
- SDK compatibility metadata;
- capabilities;
- event/public/management API identifiers;
- Management UI Schema;
- external package entry points;
- cross-repository wire shapes.

A portable Skill must not pass only because GamerHQ application modules are
available in the same checkout.

### End-to-end / live acceptance

Use only when required to verify real external systems.

Examples:

- Discord permission inheritance;
- bot role hierarchy;
- OAuth redirects;
- VPS networking;
- SELinux mounts;
- external provider behavior.

Document these as owner/manual gates. Do not pretend offline CI proves them.

## Development workflow

During implementation:

1. Run focused tests for the owning component.
2. Add a regression test for a bug when feasible.
3. Expand to shared/integration tests when public contracts or shared helpers change.
4. Run full CI before merge for normal/architectural work.

Do not repeatedly rerun passing full suites without a new change or unresolved
risk.

## Current GamerHQ CI gates

The main repository currently checks:

- Python 3.12;
- Python 3.14;
- dependency integrity with `pip check`;
- full pytest suite;
- repository/history audit;
- shell script syntax;
- credential-free Docker Compose configuration;
- production Docker image build.

CI does not deploy.

## Formatting, linting and typing

The existing codebase does not currently enforce repository-wide formatting,
linting or static typing.

Do not introduce a broad formatting migration as a side effect of an unrelated
feature.

Recommended future evaluation:

1. evaluate `ruff check` on the current codebase;
2. evaluate `ruff format --check`;
3. establish a migration/baseline strategy if legacy findings are large;
4. consider static typing first for portable Runtime/SDK code where it provides
   the highest compatibility value.

Tool adoption should reduce risk, not create churn.

## Skill repositories

A standalone Skill should normally test:

- package installation;
- pinned/reviewed SDK compatibility;
- manifest validation;
- static capability metadata;
- Runtime conformance;
- management/event/API contracts;
- lifecycle/idempotency;
- storage behavior;
- scheduler behavior where used;
- failure/retry behavior for visible side effects.

Use the external Skill template/conformance tooling instead of importing GamerHQ
host internals.

## Database changes

Database changes require:

- migration coverage against the previous schema shape;
- preservation of existing records;
- idempotent startup/migration behavior;
- backup/rollback consideration;
- full integration tests for the owning adapter.

See `docs/DATABASE_MIGRATION.md`.

## Security-sensitive changes

Authentication, authorization, permissions, secrets, web APIs and Skill
capability changes require negative-path tests.

Examples:

- unauthorized guild rejected;
- invalid/expired session rejected;
- missing capability fails closed;
- destructive action requires review;
- stale revision is rejected;
- private resources are not exposed.

See [SECURITY.md](SECURITY.md) and the canonical root `SECURITY.md`.

## Reporting results

Always distinguish:

- tests actually run now;
- existing CI results;
- checks not available in the current environment;
- live acceptance not performed.

Never report "all tests pass" based only on an earlier unrelated run.
