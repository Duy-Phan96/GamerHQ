# GamerHQ production safety

- One active GamerHQ instance per live guild. A same-DB process lock does not cover a second machine or a different DB.
- The VPS database is production source of truth. Local databases are development only. Never copy one over production without an explicitly reviewed migration and backup.
- Production uses clean `main` at `/opt/gamerhq/app`. Use `scripts/update.sh` and `scripts/backup.sh`; never force-push or use `git reset --hard` to make deployment pass.
- `.env` remains VPS-local and private. Never print it, upload it, include it in reports or modify secrets automatically. Use the safe production doctor.
- Back up before database changes. No automatic database replacement or destructive Discord maintenance. Require explicit review/confirmation for such operations.
- Keep GamerConnect separate. Do not deploy or start a live bot as routine validation.

Follow the single [production operations guide](docs/PRODUCTION_OPERATIONS.md), including when working through optional VS Code Remote SSH.
