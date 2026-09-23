# Contributing

Start with [AGENTS.md](AGENTS.md) and the selective [development workflow](docs/DEVELOPMENT_WORKFLOW.md).
The root instructions link feature contracts; historical reports are optional background.

Create a focused branch from the agreed base, for example `git switch -c codex/fix-description`. Keep changes small, preserve current behavior and keep GamerHQ separate from GamerConnect.

Install `requirements.lock` and `requirements-dev.txt` into a local virtual environment. Use the workflow's focused/full-test guidance; pytest and `python -m tools.test` both provide offline isolation. Tests require no credentials and use synthetic data. No linter/formatter/type-checker is configured; do not introduce a repository-wide style migration for a small change.

Review `git diff` and `git diff --cached`; do not force-add excluded private/runtime files. Use the root topic index to select affected documentation rather than reading every guide. Update changed public behavior/commands/configuration and the relevant changelog entry alongside implementation; keep temporary reports and private IDs/data out of permanent user documentation.

The owner reviews releases, chooses repository licensing and performs deployment/push operations. See SECURITY.md for private reporting and RELEASE_CHECKLIST.md for live acceptance.
