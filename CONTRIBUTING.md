# Contributing

Start with [AGENTS.md](AGENTS.md), the [Software Development Lifecycle](docs/development/SDLC.md), and the selective [development workflow](docs/DEVELOPMENT_WORKFLOW.md).
The root instructions link feature contracts; historical reports are optional background.
AI-assisted development follows [SOP-008](docs/sop/SOP-008-ai-assisted-development.md), including architecture checks, diff self-review and an explicit handoff summary.

Create a focused branch from the agreed base. Preferred prefixes are `feat/`, `fix/`, `refactor/`, `docs/`, `test/`, `chore/` and `ci/`. Keep changes small, preserve current behavior and keep GamerHQ separate from GamerConnect.

Install `requirements.lock` and `requirements-dev.txt` into a local virtual environment. Use the workflow's focused/full-test guidance; pytest and `python -m tools.test` both provide offline isolation. Tests require no credentials and use synthetic data. No linter/formatter/type-checker is configured; do not introduce a repository-wide style migration for a small change.

Review `git diff` and `git diff --cached`; do not force-add excluded private/runtime files. Use the root topic index to select affected documentation rather than reading every guide. Update changed public behavior/commands/configuration and the relevant changelog entry alongside implementation; keep temporary reports and private IDs/data out of permanent user documentation.

The owner reviews releases, chooses repository licensing and performs deployment/push operations. See SECURITY.md for private reporting and RELEASE_CHECKLIST.md for live acceptance.


## Skill development

New first-party or external Skills must follow the canonical:

- [Skill Developer Guide](docs/skills/developer-guide.md)
- [Strict Authoring Contract](docs/skills/authoring-contract.md)
- [Skill Review Checklist](docs/skills/review-checklist.md)
- [SDK Conformance Check](docs/skills/conformance.md)

Start independent Skill repositories from
`examples/external-skill-template/` rather than copying GamerHQ host code.


## Commit convention

Use Conventional-Commit-style prefixes without introducing commit tooling yet:

- `feat:` backward-compatible feature;
- `fix:` bug fix;
- `refactor:` internal restructuring without intended behavior change;
- `docs:` documentation only;
- `test:` test-only change;
- `chore:` maintenance;
- `ci:` CI/workflow change.

Keep commit messages concise and focused on the changed behavior.
