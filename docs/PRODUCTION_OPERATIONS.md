# Production operations

This is the canonical day-to-day operating guide. [DEPLOY](../DEPLOY.md) covers first installation and AlmaLinux prerequisites; [database migration](DATABASE_MIGRATION.md) covers explicit snapshot import. The VPS SQLite database is authoritative for production. Local databases are development data, not replacement production state.

## Server setup

`/server setup` is an owner-only first-time wizard: Core Server → Integrations →
Roles & Permissions → Features → Review → Finish. Each section opens the existing
manager; return with `/server setup`. Finish checks persisted core resources and
records completion only when their cached structure/permissions are ready.
Completed servers show **Open Server Management** and **Run Setup Again — Advanced**.
An older installation without the completion marker needs one reviewed Finish;
the wizard never infers ownership or creates resources merely by opening it.

## Server manage

Use `/server manage` for normal administration: Server Structure, Roles & Permissions,
Integrations, Managed Messages, Features and Server Log. The landing panel does
not scan Discord messages. Structure uses the gateway cache and read-only SQLite.

**Review Structure** links existing resources after preview/confirmation. Ambiguous
matches show readable channel/category choices. **Fix Common Issues** previews
placement, scoped permissions and managed-message repairs. The owner can separately
preview missing resources. **Review Duplicate Messages** runs the existing bounded
scan and explicit pair-by-pair keep/remove confirmation; there is no bulk deletion.
After linking channels, review again to discover their messages. Normal views hide
internal statuses, mapping keys and fingerprints. Features routes to existing
feature commands; it does not enable hidden beta features automatically.

Integrations → select Instant Gaming, DealGecko, Jockie or Pancake → Select Bot →
Confirm Bot verifies the current guild bot member and persists its ID. Stored IDs
take precedence over legacy IDs and environment/bootstrap defaults. Saving an ID
does not grant permissions: review structure/fixes afterwards. Secrets remain in
the private VPS environment. External bot absence is optional, not a failed setup.

## Server dev

`/server dev` is owner-only: Health, Reconcile, Repair, Duplicate Scan, Production
Doctor, Resource Mappings and Raw Diagnostics. Health opens fast diagnostics with
the existing deeper Details action. Resource Mappings opens the mapping preview;
Raw Diagnostics opens the diagnostic findings. Production Doctor provides the
offline CLI route below, never a shell executing private configuration in Discord.
Legacy technical slash commands remain for compatibility with their existing
owner/admin checks; normal administration should start with `/server manage`.

## Internal operations

| Command | Responsibility | Writes |
| --- | --- | --- |
| `/server health` | Fast DB/config/cache diagnosis; **Details** adds message and recovery inspection | None; SQLite connections are read-only |
| `/server reconcile` | Link existing categories, channels, base/bot roles and canonical messages to their existing registry keys | IDs/managed metadata only, after owner/admin confirmation |
| Setup / Manage → Preview Missing Resources | Create genuinely missing blueprint categories/channels, base/bot roles and canonical messages | Owner-only preview and confirmation; never creates over uncertain candidates |
| `/server repair` | Fix linked resource names/placement/order, scoped permissions, bot grouping, pins and uncustomized generated message content | Owner/admin preview and confirmation; no creation or deletion |
| `/server message-duplicates` | Review duplicate canonical messages separately from structure warnings | Existing pair-by-pair explicit keep/remove confirmation |

Reconciliation classifies each record as EXACT_MATCH, SAFE_ADOPTION, AMBIGUOUS, MISSING or STALE. A single confident match is linked only after confirmation. Use **Review an existing resource**, select a candidate by ID/category/date, then **Preview Changes → Confirm Changes**. **Ignore for now** excludes that record. Cancel writes nothing. A changed resource, revoked authorization or expired preview requires a fresh scan. Unknown private resources cannot be adopted into public slots.

Run reconciliation again after linking channels to discover their messages. Setup also works in dependency stages: create missing categories/channels, rescan, then create their missing messages. Existing candidates must be reconciled first. Scans inspect bounded history; incomplete inspection blocks creation instead of assuming absence. Customized content, unknown controls, pending delivery and conflicting ownership remain for review. Repair never sends replacement messages or removes duplicate pins.

The generic operations cover the core blueprint and canonical message registry. Optional Game Areas and game-specific roles retain `/game-admin recover-existing` and their existing guarded workflows; reconciliation cannot recover lost tickets, sessions, OAuth credentials or delivery claims. Retired legacy channels/messages are retained for separate owner review, not deleted by setup/repair/reconcile. Existing specialized commands (`/server instant-gaming`, `/server sync-support`, `/server roles`, `/server adopt`) keep their narrower contracts. `/server adopt` adopts a desired public layout; `/server reconcile` links resource IDs.

Startup registers commands and persistent component handlers, initializes additive schema migrations and resumes normal member/event lifecycles. It does not refresh canonical boards or run structural repair. Existing member buttons remain available. Channel-change security enforcement and ordinary temporary-room cleanup retain their existing behavior. A restart is not an offline test.

## Responsive scans and performance diagnostics

Health, reconciliation, setup/repair previews and duplicate review acknowledge immediately and update one private status response with the result. Default health and **Refresh** use SQLite, local configuration and the Discord gateway cache; they do not fetch message history. **Details** runs the deeper, read-only Discord checks and attaches their results. `tools.production_doctor` remains the separate infrastructure/environment/database diagnostic; it does not connect to Discord.

Reconciliation/setup previews prefer persisted IDs and cached channels/roles. Each valid mapped board is fetched by its exact message ID, without scanning pins or history. Missing/stale message IDs trigger discovery only in the expected channel. Boards sharing a channel reuse one snapshot. Duplicate review explicitly inspects at most 100 pins and 100 recent messages per channel, requesting one extra of each to detect truncation. Truncation requires manual review and blocks claims that a message is absent; it never licenses creation/deletion. Legacy feature refresh and destructive migration helpers retain their duplicate/ownership guards, even for mapped messages.

Reads share a command-local cache and at most three concurrent network reads, with a 45-second timeout per read. SQLite read phases reuse one read-only connection and settings lookups; the scope ends before writes. There is no cross-command permission cache or database schema change. Confirming a plan revalidates affected resources and compares the original signatures. A message repair fetches only that board immediately before its write. Small plans for linked channels fetch the channel and relevant category; discovery/adoption, category/order and creation checks still need a fresh inventory. Repair previews also retain fresh structural permission checks. These safety checks may take longer than fast health.

The `discord.gamerhq.performance` logger emits one concise INFO line per measured operation, including previews/apply, duplicate checks, command-guide refresh, role choices, ticket recovery, LFG join, voice controls, game-area scans and deal backfill/import. Nested work contributes to the outer operation. Set this logger to WARNING through normal Python logging configuration to suppress the optional diagnostics; no new environment setting is required.

```text
operation=server_reconcile_preview duration_ms=... api_reads=... discord_api_calls=... history_scans=... db_queries=... rate_limits=...
```

`api_reads` counts scoped read jobs; a history job can issue several HTTP requests. `discord_api_calls` counts calls at discord.py's HTTP request boundary, not internal retry attempts. `rate_limits` counts rate-limit warning records observed in that operation; it is not a packet-level count of every 429. Discord.py still handles backoff/retries. Logs contain counts and operation names, not request arguments, content, SQL text or configuration values.

The offline six-board fixture in one channel reduced history scans **6 → 0**, pin scans **6 → 0**, full channel inventories **1 → 0**, and SQLite connections **332 → 1**. Six exact message lookups remain. Six boards with missing mappings share one bounded history read. These are deterministic call-count measurements, not live VPS latency guarantees. Repeated per-board discovery and full structural re-fetches were confirmed sources of request amplification; attributing a particular production 429 requires its corresponding operation logs.

Member role changes, LFG joins, tickets and voice controls stay independent of global scans. Role changes retain the fresh member lookup inside their lock to avoid stale toggle races. LFG management defers before database work. Game-area deletion retains fresh dependency checks before each destructive step. Deal imports/backfills retain their bounded input and existing disabled-provider behavior; no provider scraping or new background mutation was introduced.

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

3. Check the private Server Log and container status. If healthy, no routine repair is required. If settings need attention, open `/server manage`. Use `/server dev` and the relevant [release acceptance checks](../RELEASE_CHECKLIST.md) for a migration or affected feature. Investigate failures before retrying; preserve the saved previous image for [rollback](../ROLLBACK.md).

## Server log

Owner-reviewed setup creates/reuses `📜・server-log` in the managed STAFF category.
Normal members cannot view it. Staff can read; posting is reserved for GamerHQ
(Discord Administrators inherently bypass channel denies). No broad admin permission
is granted to GamerHQ. The channel ID uses the existing managed-channel registry.
Startup never creates/discovers a log by name; if missing or exposed, notices are
withheld and Health reports a repairable finding.

Startup announces VERSION + build commit once, with up to three bullets from the
first CHANGELOG section. `scripts/update.sh` embeds Git HEAD through `VCS_REF`;
manual builds should use the same argument. Without a known commit, deduplication
falls back to VERSION. A same-version/commit restart or reconnect does not post
again. Persistent views and database startup precede the notice. Setup completion,
confirmed management fixes and integration changes also post concise notices.
Actionable cached health findings produce a deduplicated warning with an
**Open Server Management** button. Private details and stack traces are omitted.

Delivery is reserved in SQLite before sending. Uncertain/failed sends are not
automatically retried; check private process logs. This favors no duplicates over
guaranteed delivery. Backup/import CLIs retain their safe terminal output; they do
not connect to Discord. Critical failures before login cannot post to Server Log;
container health and private process logs remain necessary. No role/LFG join spam.

## After a fresh database or migration

1. Back up the authoritative VPS DB before an explicit migration; never overwrite it with a local development DB automatically.
2. Deploy reviewed code using the update script and inspect container health.
3. Open `/server manage` → Server Structure → Review Structure.
4. Review candidates, resolve ambiguity and confirm the chosen mappings. Rescan after linking structural resources.
5. Use **Review Duplicate Messages**; other structure warnings do not block unrelated duplicate groups. **Review Structure** returns to mapping review.
6. Use **Fix Common Issues**, review the plan and confirm. Owners can separately **Preview Missing Resources**, then rescan.
7. Verify ordinary-member/private-channel access. For unresolved issues use `/server dev` → Health. Review findings are not permission to delete or recreate resources.

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

Before remote assistance read [PRODUCTION_RULES](../PRODUCTION_RULES.md): never print
`.env`, automatically modify secrets, force-push, reset hard, replace the production
DB, or delete Discord resources without explicit authorization.

## Database migration decision

The current owner-reported situation is a richer Windows database and a fresh VPS
database. **No actual snapshot comparison has been supplied for this change.**
Local/production counts and production-only tickets/messages/activity remain unknown;
there is no evidence supporting automatic replacement.

The empty Select Games response means there are no games with `active=1`, `selectable=1`
and a nonempty role mapping. `active=1` alone does not make a game visible. A fresh catalog
seeds games hidden by default; seeding updates descriptive metadata and preserves
existing visibility/role/area IDs. Missing role IDs also exclude games from this
button. Verify the running path (`/app/runtime/data/gamerhq.db` in Compose,
host `/opt/gamerhq/data/gamerhq.db`) before changing state. Wrong paths, absent
catalog rows and missing/incomplete migrations must be distinguished using doctor
and snapshot counts; the screenshot alone does not establish which occurred.

Compare consistent private snapshots first:

```bash
python -m tools.compare_databases /private/local-copy.db /private/production-copy.db
python -m tools.import_database --scope games --source /private/local-copy.db --target /private/production-copy.db --dry-run
```

The report prints known-table counts, selection/role/area state, schema differences,
production-only and changed rows, and a review token. Unknown table names are
aggregated. It does not print row contents, credentials or Discord IDs. Differences
are not proof of creation time; conservative conflicts include changed schema.
Managed-message counts include matching settings keys, so review them as inventory
indicators rather than proof that every Discord message exists.

If production contains newer/divergent state, **keep production authoritative** and
review the games-scope recovery on copies. It restores known game metadata,
selection flags and missing role mappings while preserving adopted production
roles/channels, settings, extra schema and runtime tables. Legacy category/chat/LFG/
create-voice IDs become migration hints only; no old areas are restored as active
canonical channels. There is no generic cross-table merge. Follow the
[copy-only dry-run and review](DATABASE_MIGRATION.md#games-scope-recovery-on-copies)
before any owner-approved live recovery. Apply requires the exact current review
token, shutdown attestation and a new verified backup. Do not deploy directly
before reviewing recovery output.

After the reviewed migration, VPS SQLite is the only live runtime source. Archive
the Windows DB; never resume its live bot. Verify existing Select Games controls,
game roles, private areas and mappings. Repair only reviewed issues; no hardcoded
replacement game catalog or automatic Discord cleanup is used.

## Direct VPS development

Until a separate Dev server exists, the owner may explicitly authorize a bounded
production maintenance window for low-risk work. Install VS Code Remote - SSH,
connect as the `gamerhq` account using **Remote-SSH: Connect to Host**, and open
`/opt/gamerhq/app`. Terminal commands and extensions can execute on that host;
verify the remote indicator before running anything. See the
[official Remote SSH guide](https://code.visualstudio.com/docs/remote/ssh).
Do not open `.env` or private DB rows in agent context. Read safe diagnostics and
redacted logs instead. Do not store passwords or tokens in prompts or Git.

Required sequence: inspect Git → backup before state/schema work → edit → offline
tests → build → explicitly authorized restart → smoke test → reviewed commit →
explicit push main. High-risk migrations, destructive changes and broad permission
changes should wait for the future Dev environment. A live restart is not a test.

Preparation on the VPS (owner-invoked; requires Python 3.12 or a supported newer
version on the host, installed separately if needed; never reuse the bot process):

```bash
cd /opt/gamerhq/app
git status --short
git branch --show-current
bash scripts/backup.sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -r requirements-dev.txt
```

After scoped edits:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m tools.repository_audit --history
git diff --check
git diff
```

Stop if checks fail. Before an owner-approved candidate restart, retain rollback
image and commit. A dirty build uses a unique review identifier (recorded privately
with its diff); it is a temporary maintenance candidate, not a released commit:

```bash
docker image tag gamerhq-bot:local gamerhq-bot:previous
git rev-parse HEAD > /opt/gamerhq/previous-commit
candidate="$(git diff HEAD | sha256sum | cut -d ' ' -f 1)"
docker compose build --build-arg VCS_REF="$candidate"
docker compose run --rm --no-deps gamerhq python -m tools.production_preflight --backup-dir /app/backups
```

Stage new permanent files before computing the candidate hash so `git diff HEAD`
includes them; never stage `.env`, DBs, backups or reports. **Owner approval to
restart is separate from approval to edit/build.** Then:

```bash
docker compose up -d --wait --wait-timeout 240
docker compose ps
```

Smoke test only the affected features and privacy with the single VPS bot. If it
fails, follow [rollback](../ROLLBACK.md); do not leave experiments silently running.
After successful review, stage only intended permanent files, review
`git diff --cached`, commit with a descriptive message, then rebuild with
`docker compose build --build-arg VCS_REF="$(git rev-parse HEAD)"` and perform the
owner-approved final restart. This makes the running release traceable to the
commit. Once healthy and clean, explicitly run `git push origin main`. Do not use
the normal update script to discard dirty/ahead local work; it deliberately refuses
that state. Suspend competing scheduled updates during this maintenance window.

## Future Dev server

Production remains `main` + production bot + production guild + production DB.
Future development uses `develop` + a separate Dev bot token + Dev guild + Dev DB
and separate storage/backups. Configure environment-specific bootstrap IDs; later
resource assignments remain in each DB. Never reuse production credentials, mapped
Discord IDs, private tickets or OAuth data as Dev fixtures. No Dev server, bot or
database is created by this change. After Dev exists, use it for live acceptance
before promoting reviewed changes to production.

## Game channels after recovery review

Use `/server manage` → Games. The personal selector is Popular Top 25 plus A–Z
with immediate private role changes. A game role needs no channel. The shared
GAMING category contains at most one optional text channel per game, alphabetically
ordered and restricted to its game role, staff and GamerHQ. At 10 members a private
STAFF games-log candidate asks for approval; Ignore is persisted. The soft limit
is 20, with explicit Create Anyway. No channels are created from popularity alone.

First-time setup previews GAMING and games-log alongside the existing core
structure. Legacy chat migration is owner-accessible under Server Dev. Preview
moves the recorded chat ID; other legacy resources require separate manual review.
Uncertain candidate deliveries or channel-creation reservations are retained to
prevent duplicates. Inspect Games → Candidates and private diagnostics rather than
clearing state or repeatedly retrying. These changes do not start a bot or migrate
production by themselves.
