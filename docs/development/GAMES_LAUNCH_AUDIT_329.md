# Games launch audit — Issue #329

## Current decision — 2026-11-21 legacy Production preparation

PR [#330](https://github.com/Duy-Phan96/GamerHQ/pull/330) now contains the bounded
owner-confirmed legacy observer. The earlier investigation below is historical;
its statements about no PR/commit/ready procedure describe that earlier audit.

Owner evidence confirms 150 source files and all 15 Core dependency packages.
Original build commit/image provenance remains unavailable; the two equivalent
commits are not identified as the original build. No Games collection has run.
Production and Staging Games inventories remain UNVERIFIED.

**GO for separate owner-authorized read-only collection once candidate CI passes.**
The single procedure refreshes source/core integrity, privately verifies and
requires approval of exact application/guild/container/image/DB binding, and runs
existing Games services through the isolated GET-only/RO-SQL observer. It does not
weaken normal revision checks or grant remote access. Existing Python/stdlib/OS/
Docker remain an explicit operational trust assumption; whole-image integrity is
not claimed. The cold rehearsal found `typing_extensions` outside the 15-package
lock: an independently checksum-verified observer-only wheel is loaded in memory,
not installed and not trusted from the Production filesystem.

The [collection runbook](GAMES_READ_ONLY_COLLECTION.md) contains one complete
checksum-verified owner command, receipt/abort rules and privacy requirements.
No repeated standalone metadata/dependency preflight is needed. Stop on any
integrity/import/binding failure; do not install packages or deploy to bypass it.
Normal Production release, Staging enrollment and all Discord/database mutations
retain their separate approval boundaries. PR #330 remains draft.

## Historical initial audit

Date: 21 November 2026, Europe/Berlin. **Audit incomplete; no live correction
authorized or performed.** Production is the community; GamerHQ Staging is its
functional mirror. Servertest is excluded. No Computer Use.

## Verified engineering and operational evidence

- [PR #328](https://github.com/Duy-Phan96/GamerHQ/pull/328) merged into develop as
  `e8c4c2f2bcfa375ec41fd87ea86d020763536372`. Its changes are documentation only.
  All five jobs passed in [post-merge CI 44901864835](https://github.com/Duy-Phan96/GamerHQ/actions/runs/44901864835):
  Python 3.12/3.14, staging-rhel, staging-web and staging-access.
- Fresh authenticated [Staging status 44905141991](https://github.com/Duy-Phan96/GamerHQ/actions/runs/44905141991)
  succeeded: controller `d765265612fe6aece0d91d21fa3eb5b02a359e2f`, legacy worker
  `92db47042d58055cb0debdfd83f11f8508e19809`, journal `PILOT_ACTIVE`, recovery
  required false. Its `hostPromotionCommit=8f450533...` is a promotion policy pin,
  **not evidence of the running multi-guild image**.
- Fresh authenticated [Staging health 44905533187](https://github.com/Duy-Phan96/GamerHQ/actions/runs/44905533187)
  independently returned PASS / HOST_READY, Gateway checked, actual running Host
  `1899c6f8cc498fefa0510d0e3f70453f6f1aa082`, Production unchanged. No restart was
  requested. This health receipt does not attest each game's roles/channels.
- Latest retained [tenant receipt 44777476448](https://github.com/Duy-Phan96/GamerHQ/actions/runs/44777476448)
  was collected on **20 November at 17:57 CET**. Its primary-guild projection was
  read from artifact `tenant-acceptance-44777476448` (artifact ID 14649954278).
  It records Host `1899c6f8cc498fefa0510d0e3f70453f6f1aa082`, stable previews and
  Event Manager package 0.1.2 installed/configured but disabled. This is historical
  evidence, not a current Games inventory or proof of runtime activation.
- That primary projection contains **zero `game:*` ownership references**. It
  does not export catalog names, active/selectable flags or full Games readiness.
  This absence is not proof of an empty catalog, no physical game channels, or
  which games need channels. No secondary-guild feature data was audited.
- The existing catalog preview last ran on 9 November (run 42615250462), against
  a backup. It reports catalog matches/mapping counts, not live Discord access
  policy. It cannot establish current readiness and was not rerun as a substitute.

## Production versus Staging matrix — evidence coverage

| Criterion | Production | GamerHQ Staging |
| --- | --- | --- |
| Current per-game catalog, logical identities and flags | UNVERIFIED | UNVERIFIED: existing artifact omits catalog rows |
| Role mappings and Discord existence/hierarchy | UNVERIFIED | UNVERIFIED: no game ownership references in historical receipt |
| Shared Games category and effective permissions | UNVERIFIED | UNVERIFIED: fresh Games-specific collection required |
| Canonical/legacy chats, history retention, extra grants | UNVERIFIED | UNVERIFIED: no inference from unrelated template completeness |
| Pending Show/Hide/creation reservations | UNVERIFIED | UNVERIFIED: not exported in existing receipt |
| Intended channel-bearing versus role-only launch set | UNVERIFIED | UNVERIFIED: absence of a chat does not establish owner intent |
| Protected controller admission/recovery | Not inspected; no authorized Production access | PILOT_ACTIVE; recovery clear, fresh authenticated status |
| Current Host/Gateway health | UNVERIFIED | PASS / HOST_READY, Host 1899c6f8…, Gateway checked |
| Skill package/lifecycle | UNVERIFIED | Historical primary Event Manager 0.1.2 installed/configured/disabled |

**Per-game readiness rows cannot truthfully be populated yet.** Neither the seed,
the 70-title expansion manifest nor a Staging report is an observed Production
catalog. Do not fabricate a per-game READY/missing list or compare environment-
local numeric game IDs. No title is approved for creation by this document.

## Existing behavior and coverage

Reuse `game_readiness_service.inspect_one` and the current visibility plan:
saved roles and canonical/legacy channel IDs; duplicate ownership; role hierarchy;
Games category identity/tombstones; additional visibility grants; current
overwrites; hidden policy; and pending/uncertain creation reservations. Existing
Games selector inventory intentionally lists active rows only. Launch auditing
must include inactive imported rows as well without activating them.

| Audit status | Meaning / next existing flow, after separate approval |
| --- | --- |
| READY | Current readiness checks pass; preserve IDs/content |
| NOT_SET_UP | Non-selectable game without an associated chat; not automatically a gap |
| HIDDEN | Retained chat and hide policy verified; preserve history |
| SETUP_NEEDED | Existing lifecycle reports placement, visibility, role or mapping work; inspect preview |
| REVIEW | Ownership/hierarchy/access/tombstone/metadata conflict or pending operation; never blind retry |
| UNVERIFIED | API/DB/provenance unavailable; do not treat as absent |

Pending operations retain `operationPending=true` and use REVIEW in the six-state
launch matrix. The existing administrator UI and its PENDING state are unchanged.
Games configured as role-only remain valid; the audit does not infer a desired
chat from active/selectable flags. Launch intent needs observed catalog evidence
and an approved selection, independently of technical readiness.

## Prepared offline export — not installed or live-executed

The owner subsequently approved completing/publishing the read-only collection
implementation. Current execution, legacy compatibility, privacy and protected
enrollment boundaries are documented in
[GAMES_READ_ONLY_COLLECTION.md](GAMES_READ_ONLY_COLLECTION.md). The sections below
record the initial evidence and preparation state, not a live execution receipt.

[services/game_launch_audit.py](../../services/game_launch_audit.py) is a small
operator-private report projection, not a second setup implementation or an
authorized remote endpoint. It reuses:

- existing tenant binding and current owner/admin authorization, rechecked after
  network awaits; exact expected application and guild identity;
- existing bounded `tenant_acceptance.readonly_snapshot`: live SQLite opened
  mode=ro, in-memory copy, query_only and restrictive SQL authorizer;
- one fresh channel inventory and one role inventory through the existing
  visibility snapshot; no member enumeration or message/history scans;
- `game_readiness_service.inspect_one` for each catalog row, including inactive
  rows; no apply, role assignment, setup, lifecycle, startup or migration call.

Output includes logical slug/name, local catalog identity, flags, saved role and
chat IDs, verified presence, parent, legacy chat and pending disposition. Logical
slug collisions are REVIEW; never join them across environments automatically.
No personal members, credentials, message content or raw database is exported.
Per-resource results stay operator-private; public GitHub comments get aggregate
counts and sanitized reason codes only. Effective access is accepted only under
the current readiness policy, not inferred from a role's name.

This module has **no CLI, HTTP route, SSH operation or auto-run hook**. It does
not grant access to either environment. The existing protected tenant acceptance
operation is source-pinned and covers two guilds; do not inject this module into
that operation or run its Servertest inspection to disguise a Games-only audit.

## Minimal reviewed collection path and access boundary

1. Review this projection and regressions through the normal develop PR process.
   Verify the expected Host/module compatibility before producing immutable
   source/checksum evidence. Local preparation is not a published release.
2. For Staging, extend the **existing** restricted controller with one separately
   reviewed Games-read-only operation and exact primary-guild/source/workflow
   pins. Use its operation lock, OIDC, deployment-security and redacted receipts;
   do not add arbitrary Python, shell, URL, SQL or cross-guild dispatch. No Host
   deployment/Gateway restart is needed merely to prepare the observer.
3. Production has no available authenticated Games export or general SSH access
   in this Codex session. Do not borrow the Staging key, copy credentials or
   invent a production controller. Prepare a separately owner-reviewed immutable
   operator collector for the existing Production administrative boundary,
   verifying the running container, actual mount and tenant identity read-only.
   Production source/transport availability and owner approval remain required;
   no executable Production command or live run is claimed ready here.
4. The collector must use a GET-only allowlist: current bot identity, the bound
   guild, that guild's channels/roles, and current bot/owner membership. Use
   existing transport budgets/redacted HTTP classification. Reject redirects,
   writes, foreign guilds and arbitrary endpoints. Never start a Discord Client
   or Gateway, sync commands, initialize a DB, provision a tenant or repair state.
   Suppress dotenv loading before application imports and use the existing
   verified container configuration; do not read or transfer an environment file.
5. Seal provenance (timestamp, Host/image, guild/application/DB binding, schema/
   integrity and exact observer digest). Compare logical game keys, retain
   conflicting aliases as REVIEW, and independently verify catalog counts and
   fingerprints before/after. Do not publish detailed mappings in Issue #329.

These are preparation requirements, not permission to enroll or execute a new
security operation. Existing status/health access does not authorize arbitrary
Production data access or bypass a source-pinned Staging collector.

## Prioritized gaps and next correction

| Priority | Proven gap / effect | Smallest safe next step |
| --- | --- | --- |
| P0 | Production Games evidence unavailable: launch readiness cannot be established | Owner-approved read-only collection through the existing administrative boundary |
| P0 | Current Staging per-game evidence missing: parity and exact correction targets cannot be selected | Review/enroll a primary-only Games observer using the existing controller |
| P1 | Channel-bearing launch selection not established | Review actual catalog and intended role-only entries after collection |
| P1 | Any resulting stale IDs, private-access conflicts or uncertain reservations | Use existing Manage Visible Games review/repair; preserve resources; separate owner confirmation |

No actual game-specific permission/mapping defect is proven by unavailable
evidence. Do not apply a three-game pilot, import games, reset reservations or
create channels now. Once the matrix exists, choose one verified Staging
SETUP_NEEDED game intended to have a chat, open its existing Show/repair preview,
review exact preserved IDs/access and get explicit confirmation. Then repeat the
read-only inventory and compare unchanged unrelated resources. Production
correction/release remains separately approved; #134 follows the Games baseline.

## Validation / handoff

The existing Games regressions cover stale roles/chats, hidden retained history,
additional access, wrong placement, permission loss, denied inventories, uncertain
creation, retry and idempotence. New export regressions cover the full inactive
catalog, tenant/actor rejection, logical collisions, redacted 403 failures,
preserved pending/history state, repeat determinism and SQL write denial.
The five focused suites passed **100 tests and 11 subtests** under local Python
3.14. After tightening foreign-inventory rejection to require explicit guild
ownership on every role/channel, the export's **8 tests passed again**. An initial
fixture failure was corrected by supplying role.guild on the established mock
roles; the real ownership check was retained. Existing discord.py deprecation
warnings remain. These are not live acceptance or new Linux CI for the local
uncommitted files. No permissions or live resources changed.

The branch is `feat/games-launch-readonly-audit`; no source was committed/pushed,
no PR was opened and no observer was enrolled. Existing unrelated branch-
protection documentation/evidence was preserved separately in the checkout.
Progress was recorded in
[Issue #329 comment 7062826075](https://github.com/Duy-Phan96/GamerHQ/issues/329#issuecomment-7062826075).

## Opaque Discord channel type 20: audited isolation decision (2026-11-24)

The latest owner-approved **private, sanitized** Production result has **92** raw
guild channels and **91** modeled channels, with one unmodeled channel of
protocol type **20**. The collector correctly aborts at
`GAMES_REST_MODEL_COVERAGE` with `DISCORD_CHANNEL_MODEL_INCOMPLETE`.
This is evidence of a discord.py model-compatibility boundary, not proof
that any Games resource is absent or a Database migration is needed.

**Guardrails for a future compatibility design (not implemented by this test PR):**

1. Keep the unknown REST resource opaque. Its ID may only be used privately
   for exact-set coverage and dependency intersection. Never construct a
   `TextChannel`, `CategoryChannel`, or a made-up permission model for type 20.
2. Check canonical and legacy Games channel references, Games-category mapping,
   parent relationships, collision candidates, associated managed settings and
   other channel dependencies in the existing read-only/tenant-bound snapshot.
3. Guard against missing resources influencing category/global capacity,
   parent-child inventories, same-name adoption, role/grant calculations or
   visibility. A type-20 channel is **not** a valid confirmed game chat or
   Games category.
4. Require a strict offline proof that **each** existing Games readiness outcome
   would be identical with the opaque resource included in a complete,
   lossless inventory. If any part cannot be demonstrated, retain global
   `UNVERIFIED`; never mark a game `READY`, `NOT_SET_UP` or `HIDDEN`
   based on a partial inventory.
5. Preserve the existing owner-only launcher, bounded GET transport, fixed
   tenant binding, SQLite read-only authorizer and private receipt policy.
   No Production/Discord writes or runtime dependency upgrades are permitted
   by this work.

The new synthetic test reproduces the exact 92:91 mismatch and confirms that
the diagnostic exposes only aggregate counts and protocol type, not the unknown
channel's private ID, name, parent or overwrite payload. Fixture success is
not Production readiness verification.

## Opaque-type-20 read-only partial catalog projection (offline implementation candidate)

This candidate introduces an explicitly opt-in collector compatibility branch **only**
for exactly one REST channel of protocol type 20 missing from otherwise exact
and guild-bound discord.py channel models. Any additional missing type, duplicate,
extra/foreign model, or incomplete role inventory still fails immediately.

The unknown channel is never converted into a TextChannel/CategoryChannel or
inserted into the Guild cache. Rather than using the partial Guild to determine
visibility, placement, name collisions or permissions, the projection disables
all Games readiness evaluation, treats every catalog row as **UNVERIFIED**,
sets the Games category to **UNVERIFIED**, and suppresses access/ACL assertions.
The existing read-only database snapshot and catalog integrity checks can still
produce owner-private evidence. The private projection is finalized as
`GAMES_AUDIT_UNAVAILABLE` with `DISCORD_OPAQUE_CHANNEL_TYPE20` and
`GAMES_OPAQUE_TYPE20`; **never** `GAMES_AUDIT_COLLECTED` or public
`GAMES_LEGACY_COLLECTION_COMPLETE`. This is not Games readiness clearance.

This is narrowly targeted to the sanitized 92:91 case, not general support for
new Discord channel types. No new REST endpoints, permissions, tenant scope,
data writes, deployment or owner approvals are provided. The observer requires
the same source attestation, offline Python CI, legacy security rehearsal, and
a **separately authorized** root-TTY Production read before a live outcome is
known. Future matching against staging still requires a fully verified
inventory on both sides; the comparison validator intentionally rejects
partial receipts.

## Upstream ServerHome/PartialChannel compatibility gate (2026-11-25)

[discord.py PR #10553](https://github.com/Rapptz/discord.py/pull/10553)
(open as checked) identifies Discord channel **type 20** as `ServerHome`,
and proposes a generic `PartialChannel` fallback for unrecognized guild
channel types. GamerHQ currently pins `discord.py==2.7.1`; the proposed
upstream change has **not** been shipped by that pin.

Importantly, a future library release could return a generic partial
model for type 20, causing the REST IDs and modeled IDs to match.
**Exact ID coverage is necessary but not sufficient to prove full
permission/channel semantics.** The protected collector therefore retains
`OPAQUE_TYPE20_UNVERIFIED` for one type-20 channel even if a future model
reports the same ID. Multiple type-20 channels still reject pending
separate review. Synthetic tests cover both a missing model and
a modeled fallback. The collector never hot-patches `discord.py`,
installs an upstream pull-request branch or changes Production dependencies.
No owner Production approval or audit is implied.

Before any promotion to `VERIFIED`, validate the actual upstream release,
constrained package upgrade, channel behavior/permissions, Games ancestry,
channel limits and compatibility with the source-sealed observer through
offline tests and protected owner review. Full Games readiness is not
currently established.
