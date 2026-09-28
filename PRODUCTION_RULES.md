# GamerHQ production safety

- One active GamerHQ instance per live guild. A same-DB process lock does not cover a second machine or a different DB.
- The VPS database is production source of truth. Local databases are development only. Never copy one over production without an explicitly reviewed migration and backup.
- Production uses clean `main` at `/opt/gamerhq/app`. Use `scripts/update.sh` and `scripts/backup.sh`; never force-push or use `git reset --hard` to make deployment pass.
- `.env` remains VPS-local and private. Never print it, upload it, include it in reports or modify secrets automatically. Use the safe production doctor.
- Back up before database changes. No automatic database replacement or destructive Discord maintenance. Require explicit review/confirmation for such operations.
- Keep GamerConnect separate. Do not deploy or start a live bot as routine validation.
- Temporary direct VPS development requires an owner-approved maintenance window: inspect, backup, edit, offline tests, build, explicitly approved restart, smoke test, review/commit, explicitly push. Never leave an uncommitted candidate silently running; retain rollback and rebuild the final committed version.
- Normal administration is `/server manage`, first-time configuration `/server setup`, internal diagnostics `/server dev`. Keep fast views free of automatic global message scans.
- Future Dev uses separate bot/guild/DB/secrets on `develop`; do not create it or copy production private data without a separate task.

Follow the single [production operations guide](docs/PRODUCTION_OPERATIONS.md), including when working through optional VS Code Remote SSH.
