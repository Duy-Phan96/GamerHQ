# Event Manager V2 extraction status

Source: owner-authorized cross-repository transformation, 2026-11-02;
[tracking issue #172](https://github.com/Duy-Phan96/GamerHQ/issues/172), superseding
the V1 UX in #153. This document records source evidence, not production state.

## Verified baseline

Host `develop` at `e45c62117b324ff270db2067dbe30167b41cdeb4` contains:

- `services/event_recurrence_service.py`: portable local-wall-clock recurrence;
- `services/event_series_service.py`: V1 materialization into `lfg_events`,
  capped at 365 planned instances from the series origin;
- `cogs/lfg.py`: creation, participation, invites, publication, private channels,
  cancellation, voice lifecycle and the complex V1 recurrence editor;
- `database/db.py`: V1 series, occurrences, membership, publication mappings,
  voice-notification claims and time proposals;
- `services/lobby_dashboard.py` and existing LFG services: managed views and lifecycle;
- `skill_runtime/`: public Runtime API 1, registration/lifecycle, management,
  scoped storage, scheduler and declarative Management UI Schema 1;
- reviewed immutable Recurring Posts 1.2.3, Progression 0.2.0 and Awin 0.9.3 pins;
- `hosts/gamerhq/web_platform.py`: authorized guild-scoped API and schema projection;
- `hosts/gamerhq/skill_deployment.py`: reviewed lock, no runtime installation.

Web `main` at `cb7830b91f50fd8d5ec2de75d0022e751f26aaf5` already has Discord
OAuth/BFF sessions, guild selection, install/enable/disable, catalog, health/version
projections and a shared collection editor. It has no update/uninstall/history
Host contract. The package lock, rather than older README dependency prose, is
authoritative for its Next.js/React baseline.

The new private `Duy-Phan96/gamerhq-skill-event-manager` repository owns the V2
domain. Existing Recurring Posts and Progression implementations remain unchanged.
Open queue items #164 (health presentation) and #166 (private web connectivity)
were inspected; neither authorizes production changes or supersedes this stream.

## Implemented public prerequisites

SDK 0.3.0 adds `storage.atomic`: capability-checked structural JSON compare-and-set
and bounded prefix pagination inside the existing `(guild, skill)` namespace.
CAS uses a SQLite write transaction before reading, including across processes.
`None` means absent; atomic values cannot be JSON null. Each value retains the
existing 64 KiB ceiling. `transact(checks,writes)` additionally provides bounded
multi-key atomic transactions for domain revisions, occurrence updates and effect claims.
`discord.resources.manage` exposes the trusted managed-resource port; the Host
owns category policy, ownership, privacy and Discord effects.

Collection schemas can declare `revision_path`, `expected_revision_key`,
`create_request_id_key` and `preview_required`; both revision fields must be supplied together. Web API
projection uses camelCase. Existing collection schemas remain compatible.

The deployment lock additionally accepts reviewed GitHub release wheels with
SHA-256 fragments. `python -m tools.skill_artifact_preflight` validates local
artifact identity, checksum, bounded ZIP contents and dependency metadata without
importing code. `UpdatePolicy` distinguishes pins, stable/beta, breaking changes,
capability changes and unsafe migrations. Eligibility means staging only.

## Migration preparation

On a protected **database copy**, use:

```text
python -m tools.event_manager_export --database-copy /protected/backup.sqlite --output /protected/events-v1.json
```

The exporter uses SQLite read-only mode and a consistent transaction, checks
integrity and preserves every column in all six event tables. It does not load
dotenv or connect Discord. Output contains private data; never commit it, attach
it to CI, or use it as a public issue fixture. Output creation is exclusive.

The Skill's `reconciliation_plan(snapshot)` preserves the full source snapshot,
checks identity, maps legacy recurrence to weekdays and flags orphan parents,
multiple channel candidates, visibility conflicts and legacy end conditions.
It never deletes channels or authorizes cutover. Existing per-occurrence IDs and
references remain explicit. A selected candidate still needs verified ownership.

Before cutover: make and verify a backup; freeze V1 writes; import all domain
state in one verified migration; compare IDs, row counts, member statuses,
visibility, history and mappings; bind one verified persistent area per Event;
switch exactly one domain authority; exercise restart/retry and permission tests.
Retain old channels unless separately approved for consolidation. Rollback after
new V2 writes requires a tested reverse migration or write freeze and restoration
of a matching application/database backup. Code rollback alone is insufficient.

## Current implementation and release gates

Host #173 and #174 are merged into develop. The Skill #1/#2 and Web #66 are
merged into their main branches. Host #175 integrates the installed-Skill Discord
editor, normal empty-domain installation, legacy barriers, resource hardening and
combined acceptance tests. It still requires passing final-head CI before merge.

The trusted Host now implements persistent area/card and temporary voice resources,
atomic six-table import, verified text/voice adoption and one domain authority.
Adopted legacy voice channels are retained. The portable Skill owns scheduling,
configuration, occurrences, participants, history and retained invitation codes;
legacy exclusions require explicit re-invitation. Discord and Web consume its
shared versioned API/schema. See [cutover details](event-manager-cutover.md).

The Host update worker stages reviewed SDK/Skill bytes into an isolated immutable
image, requires separate apply approval, rejects stale READY records, records
history and checks health. It rolls back the image only when application data is
unchanged, otherwise preserving data for reviewed recovery. The protected workflow
is opt-in and disabled. See [worker boundary](staged-deployment-worker.md).

Remaining release gates:

1. Approve private Skill artifact delivery and reviewed SDK/Skill release pins.
   The package is not bundled/enabled in production by these development merges.
2. Provision an authorized staging Host/Discord identity/category, protected profile,
   runner and approval environments. Host repository runner/variable/secret/environment
   inventories were empty when inspected; no configuration was changed.
3. Complete real Event Manager Docker/private-artifact and live Discord acceptance,
   with backed-up/anonymized existing six-table data, permission/restart/retry checks,
   precise resource decisions and verified recovery limits. Public-fixture Docker CI
   validates the generic staging mechanism, not live customer acceptance.
4. Obtain separate approval for the exact production image and migration/cutover
   plan. No production database, Gateway, channel or credential was changed.

Interrupted durable effect claims fail closed and need verified Host quiescence
and remote-state review. Notification reservations are not delivery confirmation;
DM dispatch, a new proposal workflow and uninstall retention controls are not claimed.
Production image/session/backup/container state has not been inspected here. The
owner's V1 acceptance-image observations remain historical input.

Release order: Host SDK contract → independent Skill artifact → reviewed Host
package pin and adapter/migration → web → staging → explicit production approval.
No release or production deployment follows implicitly from these PRs.
