# Security

Never publish Discord bot tokens, OAuth/client secrets, database credentials, private keys or webhook credentials. Keep `.env`, runtime SQLite files and sidecars, logs, backups, uploads, exports and ticket transcripts out of Git. Affiliate referral links in the support board are intentionally public and are not authentication credentials.

For now, report security issues privately to the repository owner. Do not invent or use a public issue as a place for secrets, real user IDs/activity, private invite codes or ticket contents. Use fictional data and redact screenshots/logs before sharing.

Rotate leaked credentials immediately. Adding an ignored-file rule or deleting a working file does not remove earlier commits. If a secret was ever committed: **Secret may exist in Git history and must be rotated before publishing.** The owner must decide any history rewrite; this project does not perform one automatically.

Run `python -m tools.repository_audit --history` and inspect staged changes before publishing. `--local` additionally reports potential secrets in ignored local text by filename/type only. It does not print values or inspect private database contents. The scan is heuristic, not proof that every possible credential format or private narrative has been detected. It checks available local refs, not unreachable/deleted objects or external copies of repository history.

Runtime action logs retain IDs/timestamps/status for diagnosis, not ticket descriptions or private invite codes. Tracebacks remain useful operational data and must be treated as private: do not upload raw logs. Restrict access to the runtime DB, because it contains community activity and support text. There is no automatic closed-ticket deletion or full transcript export.

Use separate development and production applications, guilds and databases. The test runner avoids loading local credentials and blocks Discord HTTP; running `bot.py` connects and reconciles real resources. Keep operating-system backups private and test restores independently of Git.
