# Games-only read-only collection â€” Issue #329

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

## Production: one owner-confirmed legacy collection

**Decision: GO for a separately owner-authorized read-only execution after the
linked candidate CI passes. No live collection has been executed.** Production
Games readiness remains UNVERIFIED until a successful private collection receipt
exists. This is compatibility evidence, not original build provenance.

### Evidence and remaining trust boundary

The owner verified the healthy existing container identity, non-root DB binding,
150 matching source files, and all 15 pinned Core packages against independent
published wheel bytes. Two commits are source-equivalent:
`6f38ddc80b4a6a3a982c16441d3c7ca3db79790d` and
`e45c62117b324ff270db2067dbe30167b41cdeb4`. Neither is asserted to be the actual
build commit. Missing OCI revision/BUILD_COMMIT data cannot be reconstructed
from source equality. `hostRevisionVerified`, `buildOriginVerified` and
`wholeImageIntegrityVerified` remain false.

The material remaining requirements are enforced together in the single launcher:
refreshed source/dependency integrity, an allowlisted cold import closure, exact
private resource identity, owner consent for that binding, and read-only execution.
The prior dependency-only receipt is **not** collection authorization. Do not
repeat the old metadata/source/dependency commands as separate prerequisites.

The existing CPython interpreter/stdlib, OS, dynamic libraries, Docker and the
owner's trusted administrative session remain the operational trust base. This
procedure does not attest the entire image or sandbox malicious native code.
Authenticated Core native modules are permitted; unknown app/package modules and
external Skill implementations are not. If the owner cannot accept that existing
operational trust base, the decision is BLOCKED: retain UNVERIFIED and use a
separately reviewed external observer over an owner-approved immutable snapshot
and GET-only inventory. Do not deploy merely to supply missing build metadata.

### Exact execution boundary

`tools/game_audit_legacy_collect.py` is an owner-operated system-Python launcher,
not a service/controller replacement and not an unrestricted remote-agent grant.
It accepts only its immutable source commit; no arbitrary code, guild, database,
SQL or ignore-revision argument exists. Normal Hosts still use
`game_audit_operator.py` and fail closed with `HOST_REVISION_UNAVAILABLE`.
Attested Hosts are rejected by this legacy route.

1. Require root in the existing trusted SSH session, a real private TTY, a
   verified non-writable root home, a dedicated mode-0700 audit directory and
   a nonblocking exclusive observer lock. Create only that audit directory when
   absent; never chmod the parent or an existing unsafe directory. Fetch exact source commits and independently verify launcher/helper
   bytes, both source-equivalent trees and all 15 Core packages. No app import,
   DB opening or Discord Games collection occurs during these checks.
2. The cold Docker rehearsal exposed an unpinned transitive import:
   `aiohttp` requires `typing_extensions` on Python 3.12. Never trust the installed
   extra package. Download the exact reviewed `typing_extensions` 4.15.0 wheel,
   verify its archive and module SHA256, and load only that verified Python source
   in observer memory. No pip install or Host filesystem change. The separate
   observer supplement is recorded in the manifest/private receipt.
3. Inspect only the fixed existing `gamerhq-bot` container. Verify running/healthy
   identity, exact image ID, non-root UID/GID, source/dependency mounts and the
   existing `/opt/gamerhq/data` binding. Resolve the configured DB with symlink,
   ownership and device/inode checks. Two bounded identity GETs verify the actual
   bot and configured guild. Staging/Servertest IDs and the shared Staging bot
   are explicitly denied.
4. Display the exact application, guild/name, container/image, database identity,
   observer/source/core/supplement fingerprints and a per-session nonce **only on
   the private TTY**. Require the displayed `COLLECT LEGACY GAMES READ ONLY <hash>`
   phrase. Cancel or any mismatch aborts. No unattended/env/CLI consent exists.
5. Recheck the approved binding and execute an ephemeral non-root process using
   `python -I -S -B`. Source/hash allowlists compile verified source directly,
   exclude pyc/site hooks/unknown packages/external Skills, and suppress dotenv.
   The legacy adapter reuses the installed Games services and DB module; the
   reviewed catalog name normalizer is supplied in memory. No Host module file
   is replaced.
6. Audit guards deny file writes, schema initialization, subprocesses, Gateway
   login/connect/start, listeners and import-time network/database access.
   Only the exact `mode=ro` DB URI and bounded in-memory snapshot are allowed.
   Snapshot reads use query_only plus a SQL authorizer: Games/settings/catalog
   metadata and channel-reference columns needed by existing dependency checks;
   no member lists, event descriptions or private message payload queries.
   The snapshot contains SQLite pages in memory only and is never exported.
7. Use only the fixed single-guild GET allowlist. No message/history lookup,
   member enumeration, other guild, redirect, proxy or HTTP mutation. At most 24
   inventory attempts/90 seconds, 2 MiB per HTTP body, 32 MiB SQLite snapshot,
   45 CPU seconds, 512 MiB address space, 128 descriptors, 120-second observer
   alarm and 150-second parent timeout. Public reference artifact fetches have
   separate size, checksum and timeout bounds; they are not Discord calls.
8. Recheck exact container/image/config and all source/Core integrity after the
   observer. Exclusive-create/fsync a private mode-0600 JSON receipt under the
   owner-private `/root/.gamerhq-games-readonly` directory (resolved from the
   root account home). Detailed IDs/maps/import names stay private;
   stdout contains only safe state/counts/opaque receipt ID. No public Actions
   artifact contains Production evidence.

A live bot may continue its normal independent writes while the observer reads;
this procedure neither freezes the bot nor claims the whole live DB is globally
unchanged. The observer opens it read-only and exports only the Games projection.

### One owner action

After reviewing the linked CI and accepting the trust boundary above, run the
following in the **existing trusted root SSH session**. This is the sole new
collection approval boundary. Inspect the private binding and type its exact
one-session phrase only if it identifies the intended Production guild, app,
image and DB. No additional root enrollment, deployment or standalone preflight
is required. Do not paste the private preview/receipt into GitHub or chat.

```bash
bash <<'GAMERHQ_LEGACY_GAMES_READONLY'
set -euo pipefail
[[ "$(id -u)" == 0 ]] || exit 1
umask 077
observer=$(mktemp)
trap 'rm -f -- "$observer"' EXIT
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --max-time 60 --max-filesize 65536 --output "$observer" 'https://raw.githubusercontent.com/Duy-Phan96/GamerHQ/a68829aa51e3769c60327be586432b44672e88f0/tools/game_audit_legacy_collect.py'
printf '%s  %s\n' '36f2a7d9bdf4671e4361c696e9290f1872bf164408a969b682c271cbf86fba52' "$observer" | sha256sum --check --status
/usr/bin/python3 -I -S -B "$observer" --source-commit a68829aa51e3769c60327be586432b44672e88f0
GAMERHQ_LEGACY_GAMES_READONLY
```

Expected successful public state: `GAMES_LEGACY_COLLECTION_COMPLETE`, aggregate
`READY/NOT_SET_UP/HIDDEN/SETUP_NEEDED/REVIEW/UNVERIFIED` counts, opaque
`privateReceiptId`, `collectionExecuted=true`, and all change flags false.
Original Host revision/build provenance stay false. A successful collection may
still report individual games UNVERIFIED/REVIEW; that is not permission to repair.

### Abort and compensation

Abort on a stale/changed binding, source/package mismatch, unsupported CPython
3.12 Linux x86_64 layout, unknown import, unsafe DB identity, owner cancellation,
quota/rate/response/schema failure, read-only guard violation or failed postcheck.
Keep inventory UNVERIFIED; the private receipt retains bounded diagnostics when
available. Never resolve a failure with pip install, broader privileges, database
repair, a bot restart or skipped validation. Stop the ephemeral observer on its
bound timeout; remove only public scratch downloads. No DB restore, image rollback,
Gateway manipulation or Discord compensation is necessary because the operation
performs no application writes. Root-private audit directory, receipt and zero-byte observer lock
are audit metadata only.

### Verification evidence

The disposable Docker rehearsal uses exact archived legacy source and authentic
Core dependency files, real DB/Games services, synthetic DB/Discord fixtures,
a read-only filesystem and disabled container networking. Tests cover a positive
collection plus write-on-import, schema initialization, external Skill import,
source tampering, Gateway startup and HTTP POST rejection. Unit regressions cover
wrong/absent TTY approval, one-session serialized consent, private receipts,
foreign DB/guild, query column scope, redirects, role/channel readiness and normal
revision enforcement. A fixture rehearsal is not live Production acceptance.

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
