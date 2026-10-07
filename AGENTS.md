# GamerHQ repository instructions

## Scope and working state
- GamerHQ and GamerConnect are separate projects. Do not modify GamerConnect unless explicitly requested.
- Inspect git status/diff and the affected implementation/tests first. Preserve pre-existing work.
- Keep changes task-scoped. Reuse existing services, DB helpers, config and UI patterns; do not add a parallel subsystem.
- Avoid opportunistic refactors. Explain a larger refactor if required for the task.

## Invariants
- Persisted SQLite/config is authoritative for managed resources where implemented. Prefer stored Discord IDs; names are fallback discovery, not proof of ownership.
- Setup/sync/repair must be retryable and idempotent: no duplicate channels, roles, messages, overrides or records. Treat ambiguity as manual review.
- Preserve private/Staff visibility and least privilege. Never make private state public through repair, migration or fallback.
- Preserve unrelated channels, messages, pins, custom overrides and user content. Destructive operations retain existing confirmation/dependency checks.
- Keep secrets, runtime DBs, logs, backups, transcripts and private user data out of source/output. Use existing environment/config for deployment-specific IDs and settings for managed runtime IDs.
- Keep health/inspection read-only. A real bot start changes Discord state; it is not a test.
- Production is `/opt/gamerhq/app` on `main`; `/opt/gamerhq/data` is authoritative runtime state. Follow [production rules](PRODUCTION_RULES.md) and [operations](docs/PRODUCTION_OPERATIONS.md) for Remote SSH work.
- Never read/expose real `.env` values. Back up before DB/schema work; no local + VPS bot against the live guild, force-push, `reset --hard`, automatic deploy or uncontrolled Discord deletion.
- Normal administration starts at `/server manage`; first-time setup is `/server setup`, technical tools belong under owner-only `/server dev`. Preserve the existing confirmed operation services.

## Development lifecycle
- Follow [docs/development/SDLC.md](docs/development/SDLC.md) for normal/architectural work.
- Follow [Repository Ownership and Handoffs](docs/development/REPOSITORY_OWNERSHIP_AND_HANDOFFS.md): this project modifies only the GamerHQ repository. Inspect other repositories read-only when needed; request changes through an explicit handoff prompt instead of editing them.
- AI-assisted work follows [SOP-008](docs/sop/SOP-008-ai-assisted-development.md): inspect → restate → architecture check → plan → smallest coherent change → test → diff self-review → human summary → PR.
- Use the central Definition of Done proportionally; do not add bureaucracy to trivial changes.
- When another repository is required, finish the GamerHQ-owned portion, document the blocker and produce a handoff. The target repository owns its implementation and release.
- Use ADRs only for durable architectural decisions; see [ADR guidance](docs/architecture/decisions/README.md).

## Validation and handoff
- Run relevant offline tests for behavior changes; add regression coverage for bugs, authorization, retries and persistence where affected.
- Use pytest or tools.test, never raw unittest discovery that bypasses isolation. See the workflow and tests/AGENTS.md.
- Do not hide existing failures. Report what ran, results, limitations and any tests not run.
- Run git diff --check and git status --short before completion. Check affected documentation links.
- Do not commit or push unless explicitly requested. Do not deploy or connect a live bot as routine validation.
- Report changed files, implementation summary, tests/results, architecture/security/migration impact, material assumptions/risks, follow-ups and owner actions.
- End with OWNER ACTION REQUIRED and exact applicable commands.

## Read selectively
Start with the relevant row; do not load every document or historical report.
| Task | Reference |
| --- | --- |
| SDLC / Definition of Done | [Software Development Lifecycle](docs/development/SDLC.md) |
| Repository ownership / handoffs | [Repository Ownership and Handoffs](docs/development/REPOSITORY_OWNERSHIP_AND_HANDOFFS.md) |
| GamerHQ server release snapshot | [Server Release Snapshot](docs/development/SERVER_RELEASE_SNAPSHOT.md) |
| AI-assisted development | [AI Development SOP](docs/sop/SOP-008-ai-assisted-development.md) |
| Workflow, validation, prompt examples | [Development workflow](docs/DEVELOPMENT_WORKFLOW.md) |
| Module ownership and startup | [Architecture](docs/ARCHITECTURE.md) |
| Channels, roles, IDs and repair | [Server structure](docs/SERVER_STRUCTURE.md) |
| Visibility and overwrites | [Permissions](docs/PERMISSIONS.md) |
| Managed pins and editor | [Managed messages](docs/MANAGED_MESSAGES.md) |
| Game library / optional areas | [Game system](docs/GAME_SYSTEM.md) |
| Lobbies, invites and event lifecycle | [LFG/events](docs/LFG_EVENTS.md) |
| External bots/services | [Integrations](docs/INTEGRATIONS.md) |
| Commands / operations | [Commands](docs/COMMANDS.md), [deployment](DEPLOY.md), [release checklist](RELEASE_CHECKLIST.md) |

Documentation describes the working tree, including uncommitted changes; it is not evidence of deployment. Verify current code when details disagree.
