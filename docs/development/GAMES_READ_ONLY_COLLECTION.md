# Games-only read-only collection — Issue #329

This prepares collection, not a deployment, repair or permission grant. No
collector has run against Production or Staging under this change. The existing
Games setup/Show/Hide flows remain the only correction mechanism. Servertest is
excluded. Do not initialize a database, load a real .env, sync commands, start a
Gateway, enable Skills or create Discord resources to complete this audit.

## Reviewed implementation boundary

- `services/game_launch_audit.py` reuses the installed Games readiness and
  visibility checks. All catalog rows are inspected, including inactive imports.
  Pending operations remain REVIEW, denied reads remain UNVERIFIED, and role-only
  intent remains unverified until the owner selects the actual launch scope.
- `services/game_audit_context.py` uses the existing DB read session and tenant
  snapshot contract: bounded 32 MiB in-memory copy, mode=ro source, query_only,
  restrictive SQLite authorizer. It never imports a replacement DB implementation.
- Modern Hosts require an exact existing primary tenant binding. The legacy
  single-guild adapter is Production-only and additionally requires the installed
  `config.GUILD_ID` and authoritative configured DB path to match. It cannot be
  selected on a modern tenant-aware DB. Schema/integrity failures stop collection.
- `tools/game_launch_audit_collect.py` fetches only the bot identity, one guild,
  its channels/roles and the current bot/owner membership. Six successful GETs,
  a per-run immutable inventory, bounded bodies/time/retries, rate-limit holds,
  no proxy inheritance or redirects. No `/users/@me/guilds`, other-guild reads,
  message/history requests, member enumeration or HTTP mutation is permitted.
- The report records names/aliases, active/selectable flags, recorded role/chat/
  legacy IDs, role hierarchy, category and channel overwrites/effective access,
  and pending reservations. Member-specific overwrite identities are redacted;
  only their count is exported. Member lists, private message content, tokens
  and raw database rows never enter the receipt.
- The observer bundle is source-commit/SHA256 sealed in `game_audit_execution`.
  It overlays only reporting code in a short-lived process inside the existing
  container, as its non-root runtime identity. Dotenv is suppressed before app
  imports. No file inside the application, secret mount or DB is replaced.

`READY`, `NOT_SET_UP`, `HIDDEN`, `SETUP_NEEDED`, `REVIEW`, `UNVERIFIED` describe
technical observed state, not authorization to create a chat. Channel-less games
may intentionally remain role-only. A missing item in a bot-visible inventory is
not proof that no inaccessible resource exists; reviewed setup retains the
existing ambiguity/ownership checks and confirmation requirements.

## Staging: existing protected controller, closed by default

The additive `games-audit` operation is SECURITY-classified, not a routine mutate
operation. It requires the existing operation lock, enabled/bootstrap state,
clear recovery latch, PILOT_ACTIVE journal, exact active Host, current health,
exact primary guild/application and a separate root-owned `gamesAudit` policy.

The pin has exactly `sourceCommit`, `sourceSha256` (all five observer files), and
`workflowSha256`. The workflow subject must be the exact new
`staging-games-audit.yml@refs/heads/main`; OIDC, replay protection, rate limits and
deployment-security owner review are unchanged. Current installed controllers
and policies have no such enrollment, and reject this operation.

The proposed workflow is preparation only: reviewed main registration and
root-owned controller/source enrollment are separate owner-approved steps.
Registering a workflow is not sufficient to grant the operation. No source
installation, root-owned policy update, launcher replacement, SSH change or
workflow dispatch is performed by this PR. Existing source-integrity manifests
must include the new handler/shared execution helper; do not blindly overwrite
the installed controller or reuse an older enrollment command.

Detailed Staging receipts stay under the already private controller worker root,
named `games-audit-<receipt-id>.json`, exclusive-create mode 0600. This is audit
metadata, not application/SQLite state. Eight-receipt quota fails closed; no
automatic deletion/rotation. The public response contains only aggregate counts,
stable sanitized errors and an opaque receipt ID. No public artifact uploads.
An owner with existing authorized VPS access can compare the private receipts;
the agent's restricted key does not gain arbitrary file access.

## Production: existing owner administrative boundary

**Exact missing capability:** this Codex session has no authenticated Production
Games endpoint or permission-scoped Production command/SSH identity. Staging
credentials, environment secrets and the Staging policy cannot authorize it.
No new SSH identity, Docker/root grant or copy of the Production token is proposed.

`tools/game_audit_operator.py` is a standalone system-Python-3.9-compatible
owner launcher for the existing Docker administrative context. It verifies its
own bytes against the explicitly selected immutable Git commit before proceeding.
Its Production target is the existing documented `gamerhq-bot` container.
Production's Dockerfile uses `USER gamerhq` and `/app/BUILD_COMMIT`, while Staging
overlays also supply OCI labels. The metadata probe verifies the actual non-root
UID/GID and reads only that regular, non-symlink build marker. Label/marker
disagreement is rejected. Missing valid revision evidence is explicit
`hostRevisionVerified=false` and prevents full collection; no image rebuild or
deployment is attempted to supply missing metadata.

The first owner step is **metadata preflight only**: inspect filtered container
identity, running/health state, revision/image, non-root user, workdir and mounts.
It does not read `.Config.Env`. A fixed standard-library-only metadata probe
runs under the container's existing non-root user and reads only `GUILD_ID`,
`GAMERHQ_DB_PATH` and the configured file's stat (not contents). It calls no
Discord API, opens no SQLite connection, imports no app/config and changes no
policy or service. Keep this output private;
do not paste raw Production metadata into public GitHub comments.

Immutable prepared observer/client source:
`b1f38fe19a86c6a8fa31f8273d2503d6e2649d11`.
The proposed Staging workflow checks out that exact commit. The owner launcher
at that revision has SHA256
`bb84782fe9b53872ee85e7e286af553073797d32e35b2003fdbe5f17889f1f3d`.

After PR/CI review and **separate owner approval of metadata preflight**, the
following complete command is for the existing trusted root SSH session on the
existing VPS. It is provided for owner review, not executed by Codex:

```bash
bash <<'GAMERHQ_GAMES_METADATA_PREFLIGHT'
set -euo pipefail
[[ "$(id -u)" == 0 ]] || exit 1
umask 077
observer=$(mktemp)
trap 'rm -f -- "$observer"' EXIT
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --max-time 60 --max-filesize 32768 --output "$observer" 'https://raw.githubusercontent.com/Duy-Phan96/GamerHQ/b1f38fe19a86c6a8fa31f8273d2503d6e2649d11/tools/game_audit_operator.py'
printf '%s  %s\n' 'bb84782fe9b53872ee85e7e286af553073797d32e35b2003fdbe5f17889f1f3d' "$observer" | sha256sum --check --status
/usr/bin/python3 "$observer" --source-commit b1f38fe19a86c6a8fa31f8273d2503d6e2649d11 --environment production --container gamerhq-bot --preflight
GAMERHQ_GAMES_METADATA_PREFLIGHT
```

Expected success state: `GAMES_OWNER_PREFLIGHT`, `collectionExecuted=false`.
Keep the receipt private. A rejected preflight reports a stable stage/reason and
does not repair anything. This command does **not** enroll the Staging operation
or authorize the subsequent full Production collection.

### Legacy Production revision provenance — collection remains blocked

The owner metadata preflight on 21 November 2026 returned a healthy non-root
Production container but `hostCommit=null`, `buildCommit=null` and
`hostRevisionVerified=false`. Full collection still fails with
`HOST_REVISION_UNAVAILABLE`. These nulls alone do not prove that the marker file
is absent: the existing inspector also returns null for `unknown` or malformed
contents. No Production commit is established by this receipt.

Repository and GitHub evidence reviewed for this blocker:

- The Production Dockerfile never declares an OCI revision label. The label in
  Staging overlays cannot be assumed on Production.
- `f00c29227cfb4e7d822ff8c13105efd4af03bc8e` introduced `ARG VCS_REF=unknown`
  and `/app/BUILD_COMMIT` on main's history. Its parent Dockerfile lacks both.
  A newer manual build without the argument also yields an unknown revision.
  Neither possibility proves which Dockerfile built the running container.
- `scripts/update.sh` supplies Git HEAD through VCS_REF. CI's ordinary
  `docker compose build` does not, and its local test image is not published as
  a Production deployment artifact. No Host image publishing/attestation step
  was found in the inspected workflows.
- GitHub's release/tag listing contains the Skill SDK wheel release, not a Host
  image-to-commit attestation. Queries for deployments in `production` and
  `Production` returned zero records on 21 November. This does not prove that
  an owner deployment never occurred outside GitHub.
- The existing rollback guide leaves the checkout on main while restoring a
  previous image. `/opt/gamerhq/previous-commit` and the mutable `:previous` tag
  are recovery clues, not a cryptographically sealed running-image/commit pair.
  The release snapshot document is a template, not a completed deployment record.

**Smallest next read:** `tools/game_audit_provenance.py` extends the existing
owner-operated inspection tooling with a source-only evidence command. It loads
the existing owner inspector from the same immutable verified Git source, calls
no configuration/DB probe and never imports application modules. Under the
already configured non-root container identity it hashes only these public
source scopes: top-level bot/config/release Python, VERSION/CHANGELOG and
requirements; Python files in cogs/database/services/tools/skill_runtime/hosts/
skills; the two repository seed/catalog JSON files. It classifies only the fixed
BUILD_COMMIT marker as ABSENT, UNKNOWN_DEFAULT, VALID_HEX or INVALID.

It does not read `.env`, environment values, SQLite files/sidecars, backups,
messages, logs, member records or installed dependency packages. It never calls
Docker run/start/stop/build, Compose, the Gateway or Discord. No Production
receipt/file is written. Temporary downloaded public Git source is removed.

The operation searches bounded, explicitly unshallowed public main/develop
history as **candidate discovery**, not assumed deployment identity. It requires
exact selected path-set/Git-blob equality and independent SHA256 verification
of every matching file. Additional/missing/modified selected source files do not
match; source mounts, symlinks, nonregular files and changed observations fail
closed. Two independent source reads and before/after container inspection must
agree on exact container ID, image ID, runtime user and mounts. The public receipt
contains a SHA256 commitment to the exact container/image pair, the source
manifest hash, counts, public candidate commits and discovery refs; no raw image,
container, mount, source contents or per-file manifest is printed or uploaded.

`SOURCE_EQUIVALENCE_ONLY` means exactly that. Even one selected-source match does
not prove the original build commit, build recipe, complete image, dependency
set, current in-memory code or clean build context. Multiple documentation-only
commits can have identical selected sources. All matches remain visible; never
choose the newest commit. Off-branch/deleted history and custom builds can yield
NO_SOURCE_MATCH. No branch, tag, VERSION or matching source set is automatically
promoted to an authoritative production commit.

The receipt always retains `hostRevisionVerified=false`,
`buildOriginVerified=false` and `collectionExecuted=false`. It cannot clear or
satisfy the Games collector's revision gate. Review the evidence before choosing
any subsequent provenance attestation procedure. A rebuild/restart solely to
add metadata is outside this authorization. Do not run full Games collection.

Full collection is a separately authorized operation requiring all of:

1. Reviewed source commit and checksum-verified launcher; actual preflight
   metadata, not paths/images assumed from Staging.
2. Exact expected Host commit/image, guild/application identities and DB path
   approved privately by the owner. The DB must reside on the existing verified
   `/opt/gamerhq/data` bind mount. Unsupported layouts stop for review.
3. Explicit phrase `COLLECT GAMES READ ONLY`, and a new output file in an existing
   owner-private directory (root-owned, no group/other access). No overwrite.
4. Source equality for the installed DB/Services/config against the immutable
   declared Host commit; no deployment or runtime source replacement to make it
   pass. Unknown/dirty sources or absent readiness capabilities stay UNVERIFIED.
5. The sealed observer running through `docker exec` under the existing configured
   non-root user. Before/after container/image/mount identity must agree. DB
   integrity is checked on the in-memory snapshot; no migration/init/restore.

The private receipt is created mode 0600 only after validation. Public stdout is
limited to success/unavailable metadata; subprocess output, API responses and
arbitrary exception text are never forwarded. Unknown errors include a stable
execution stage. If no compatible running Host is verified, stop; no fallback
container, new guild or expanded access is permitted.

## Offline comparison and privacy

Run `python -m tools.game_launch_audit_report --help` in the reviewed development
environment for the two private input filenames and new private output filename.
The inputs are the **collected receipts**, never database files. Its CLI only
reads JSON and writes a new local mode-0600 report; it makes no API/DB calls.

Comparison uses the existing catalog `name_key` normalization and alias overlap.
GTA V aliases may match GTA Online when supported by recorded aliases. Any
one-to-many/colliding match is REVIEW; it is not silently linked. Discord IDs and
environment-local catalog IDs do not determine parity. The report retains both
environments' original IDs privately and compares flags, readiness, role safety,
effective access and operation state. Intent remains a separate owner decision.

Only aggregate counts/sanitized status belong in GitHub Actions or Issue #329.
Never upload Production or detailed Staging receipts to public repo artifacts,
post channel/role maps, expose member overwrite IDs, or copy a Production DB into
Staging. Private receipts must be compared through the existing owner boundary
or a separately approved private evidence channel; no such new access is granted
by this PR. Operator-private files use `*.private-games.json`, ignored by Git.

## Validation and activation checklist

- Fixture tests: full inactive catalog, stale IDs/ownership/hierarchy, denied
  inventory, pending state, preserved hidden history, alias collision, distinct
  environment IDs, SQL write denial, GET allowlist and private output handling.
- Cold source-sealed subprocess rehearsal uses the installed fixture DB and
  real Discord models; no Gateway, dotenv, live HTTP or provisioning.
- OIDC regression requires the exact main workflow, deployment-security and owner
  approval. Current un-enrolled controllers fail closed.
- Verify Linux CI for the exact PR head; a local mock is not live acceptance.
- Owner reviews before immutable main workflow registration/controller enrollment.
- Owner verifies Production metadata privately before separately authorizing
  collection. Do not invent a complete live command from unknown bindings.
- Protected live collection requires additional approval and no setup/repair.
- Publish a redacted Production/Staging readiness matrix; then separately review
  the first existing Show/repair preview in Staging. Production remains untouched.

Tracks [Issue #329](https://github.com/Duy-Phan96/GamerHQ/issues/329), follows the
[roadmap](GAMERHQ_PRODUCT_ROADMAP.md), and preserves the existing
[Games lifecycle](../GAME_SYSTEM.md). Historical audit evidence remains in
[GAMES_LAUNCH_AUDIT_329.md](GAMES_LAUNCH_AUDIT_329.md).
