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
existing 64 KiB ceiling. This is not a multi-key transaction API.

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

## Remaining engineering work and release gates

1. Public Host managed-resource contract: tenant/Skill-scoped persistent text
   area, overview and per-occurrence voice; enforced visibility/access policy,
   ownership verification, retry recovery and no uncontrolled resource creation.
2. Transactional V1 import and single-authority cutover, including all legacy
   participant statuses, invitation/share references, proposals and voice claims.
3. Discord adapter replacement and unified full event editing. V1 remains the
   running implementation until this is accepted; it is not a permanent second
   V2 implementation. The V2 package is not bundled or enabled by this change.
4. Artifact publication/trust review, staged installation/deployment orchestration, staged image build,
   migration preflight, deployment history and health-driven rollback. The new
   artifact verifier/policy is preparation, not an automatic deployment system.
   The companion `feat/skill-release-proposals` branch adds Host-owned daily
   discovery and draft update PRs for locked publisher repositories. It does not
   automatically merge, stage, deploy, run migrations or roll back production.
5. Host update/uninstall/history APIs; web participant/history and resource UX.
6. Python 3.12/3.14 CI, Docker staging, production inspection and live acceptance.

The V2 preview must report degraded health while Discord delivery/cutover are
unavailable. Production image, token/session count, backups and container health
have **not** been inspected from this development environment. The owner's V1
acceptance-image observations are historical input, not new verification.

Release order: Host SDK contract → independent Skill artifact → reviewed Host
package pin and adapter/migration → web → staging → explicit production approval.
No release or production deployment follows implicitly from these PRs.
