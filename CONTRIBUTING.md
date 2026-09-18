# Contributing

Create a focused branch from the agreed base, for example `git switch -c codex/fix-description`. Keep changes small, preserve current behavior and keep GamerHQ separate from GamerConnect.

Install the pinned `requirements.txt` into a local virtual environment. Run `python -m tools.test`, `python -m pip check` and `python -m tools.repository_audit --history` before requesting review. Tests require no credentials and use synthetic data. No linter/formatter/type-checker is configured; do not introduce a repository-wide style migration for a small change.

Never commit secrets, runtime databases, logs, backups, user uploads or transcripts. Review `git diff` and `git diff --cached`; do not use force-add to bypass privacy exclusions. Add focused regression coverage where behavior or safety changes. Update documentation when commands/configuration change.

The owner reviews releases, chooses repository licensing and performs deployment/push operations. See SECURITY.md for private reporting and RELEASE_CHECKLIST.md for live acceptance.
