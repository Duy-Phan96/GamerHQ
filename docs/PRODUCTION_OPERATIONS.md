# Production operations

This is the canonical day-to-day operating guide. [DEPLOY](../DEPLOY.md) covers first installation and AlmaLinux prerequisites; [database migration](DATABASE_MIGRATION.md) covers explicit snapshot import. The VPS SQLite database is authoritative for production. Local databases are development data, not replacement production state.

## Choose the right Discord operation

| Command | Responsibility | Writes |
| --- | --- | --- |
| `/server health` | Read-only diagnosis: PASS, WARNING, REPAIRABLE, RECONCILE, MANUAL REVIEW, CRITICAL | None; SQLite connections are read-only |
| `/server reconcile` | Link existing categories, channels, base/bot roles and canonical messages to their existing registry keys | IDs/managed metadata only, after owner/admin confirmation |
| `/server setup` | Create genuinely missing blueprint categories/channels, base/bot roles and canonical messages | Owner-only preview and confirmation; never creates over uncertain candidates |
| `/server repair` | Fix linked resource names/placement/order, scoped permissions, bot grouping, pins and uncustomized generated message content | Owner/admin preview and confirmation; no creation or deletion |
| `/server message-duplicates` | Review duplicate canonical messages separately from structure warnings | Existing pair-by-pair explicit keep/remove confirmation |

Reconciliation classifies each record as EXACT_MATCH, SAFE_ADOPTION, AMBIGUOUS, MISSING or STALE. A single confident match is linked only after confirmation. Use **Review an existing resource**, select a candidate by ID/category/date, then **Preview Changes → Confirm Changes**. **Ignore for now** excludes that record. Cancel writes nothing. A changed resource, revoked authorization or expired preview requires a fresh scan. Unknown private resources cannot be adopted into public slots.

Run reconciliation again after linking channels to discover their messages. Setup also works in dependency stages: create missing categories/channels, rescan, then create their missing messages. Existing candidates must be reconciled first. Scans inspect bounded history; incomplete inspection blocks creation instead of assuming absence. Customized content, unknown controls, pending delivery and conflicting ownership remain for review. Repair never sends replacement messages or removes duplicate pins.

The generic operations cover the core blueprint and canonical message registry. Optional Game Areas and game-specific roles retain `/game-admin recover-existing` and their existing guarded workflows; reconciliation cannot recover lost tickets, sessions, OAuth credentials or delivery claims. Retired legacy channels/messages are retained for separate owner review, not deleted by setup/repair/reconcile. Existing specialized commands (`/server instant-gaming`, `/server sync-support`, `/server roles`, `/server adopt`) keep their narrower contracts. `/server adopt` adopts a desired public layout; `/server reconcile` links resource IDs.

Startup registers commands and persistent component handlers, initializes additive schema migrations and resumes normal member/event lifecycles. It does not refresh canonical boards or run structural repair. Existing member buttons remain available. Channel-change security enforcement and ordinary temporary-room cleanup retain their existing behavior. A restart is not an offline test.

## Normal release and update

1. In the development checkout, run tests, dependency checks and the repository audit; review and commit the intended changes. Promote the reviewed release to `main`, then explicitly push it.
2. On the VPS as `gamerhq`, use the canonical update entry point:

```bash
cd /opt/gamerhq/app
bash scripts/update.sh
docker compose ps
docker compose logs --tail=100 gamerhq
```

The script requires clean `main`, the official origin, the expected deployment path and a private mode-600 `.env`. Under a maintenance lock it saves the previous image/commit, backs up the database using the old image, fetches and runs `git pull --ff-only origin main`, builds the candidate image, validates the private environment and rehearses DB migrations on a temporary copy. Only then does it run Compose with a bounded health wait and show final status. Building before the file validator avoids requiring Python dependencies on the VPS host; validation still happens before replacing the running bot. The script does not run Discord setup/repair/reconciliation.

3. Inspect logs privately, then run `/server health` and the relevant [release acceptance checks](../RELEASE_CHECKLIST.md). Investigate failures before retrying; preserve the saved previous image for [rollback](../ROLLBACK.md).

## After a fresh database or migration

1. Back up the authoritative VPS DB before an explicit migration; never overwrite it with a local development DB automatically.
2. Deploy reviewed code using the update script and inspect container health.
3. Run `/server health`, then `/server reconcile`.
4. Review candidates, resolve ambiguity and confirm the chosen mappings. Rescan after linking structural resources.
5. Run `/server message-duplicates`; other structure warnings do not block unrelated duplicate groups. **Open Reconciliation** returns to mapping review.
6. Run `/server repair`, review the plan and confirm. Use owner `/server setup` separately only for genuinely missing resources, then rescan.
7. Run `/server health` again and verify ordinary-member/private-channel access. Manual-review findings are not permission to delete or recreate resources.

## Backups

The canonical script is `scripts/backup.sh` (there is no root `backup.sh`):

```bash
cd /opt/gamerhq/app
bash scripts/backup.sh
```

The existing systemd timer invokes this script. It coordinates with updates, creates a verified SQLite snapshot, keeps 14 daily snapshots and preserves manual/pre-update backups. See [timer installation](../DEPLOY.md#daily-backups) and [restore rehearsal](../ROLLBACK.md). Keep backups private and maintain encrypted off-host copies.

## Safe configuration and database diagnostics

[.env.example](../.env.example) is the configuration schema. Real values stay in `/opt/gamerhq/.env`; do not print or upload it. Doctor reports configured/missing/enabled/disabled, never token or ID values. The database path is intentionally reported. SQL inspection is read-only: existence, writability, schema version/columns, quick integrity check, backup directory and recent backup age. It does not migrate or create a database. A diagnostic warning does not imply permission to repair.

On the VPS, validate the original file through stdin so duplicate keys remain visible without exposing values or mounting a mode-600 secret file into the container:

```bash
cd /opt/gamerhq/app
docker run --rm -i --network none --entrypoint python gamerhq-bot:local -m tools.production_doctor --env-stdin-only < ../.env
docker compose run --rm --no-deps gamerhq python -m tools.production_doctor --backup-dir /app/backups
```

The second command uses Compose's process environment and persistent data mounts; no `.env` file inside the image is expected. Environment-file validation reports duplicate key names and line numbers without values; `production_preflight` uses the same parser when given a file. A process environment alone cannot detect duplicates already collapsed by Compose. `--env-only` and `--env-stdin-only` return failure on parsing/duplicate problems. Full doctor output is diagnostic; review warnings even when its process exits successfully.

If Python and project dependencies are already installed on the host, this optional command also inspects Docker without starting a container:

```bash
python -m tools.production_doctor --env-file /opt/gamerhq/.env --db-path /opt/gamerhq/data/gamerhq.db --backup-dir /opt/gamerhq/backups --docker
```

Docker output is restricted to health, restart policy and expected mount destinations; raw `docker inspect`, environment values and exception output are never forwarded. Without Docker the check reports unavailable. Do not mount the Docker socket into the bot. Doctor/preflight do not connect to Discord.

## One active instance and optional Remote SSH

Keep one GamerHQ process per live guild. The OS-held lock beside the resolved DB rejects another process using that same database and releases on process exit. It does not coordinate different databases or machines; stop the local production-credential bot before starting the VPS bot.

Development stays local/on development branches; production stays on `main` at `/opt/gamerhq/app`. VS Code Remote SSH is optional and is not required for normal operation. Before any future remote assistance read [PRODUCTION_RULES](../PRODUCTION_RULES.md): never print `.env`, automatically modify secrets, force-push, reset hard, replace the production DB, or delete Discord resources without explicit authorization. Back up before DB changes and share safe diagnostic summaries instead of private runtime data.
