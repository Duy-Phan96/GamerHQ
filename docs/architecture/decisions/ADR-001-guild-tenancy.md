# ADR-001 Guild tenancy for the shared GamerHQ application

Status: Milestone 2 protected two-guild Staging pilot active; PR #317 merged into develop (2026-11-20). Milestone 3 generalized enrollment is proposed, not active.

Product direction: One public Discord application serves independent guilds.

## Context

The owner can choose Gaming Community, start directly with Skills, or leave
the guild unchanged. A template is optional. Marketplace, KAMEX, Affiliate,
Streamer and other specialized features are separate decisions. A Skill owns
its dynamic resources and domain data.

The current Host syncs commands to `GUILD_ID` and clears global registrations.
One process loads the same cogs for every connected guild and uses one SQLite
file and the configured global fallbacks. A second membership alone cannot
serve an independent customer safely.

## Decision

Keep one Host and the existing Discord UI, Skill SDK, ownership checks and
preview/confirmation services. Begin with an explicitly admitted cohort.
Derive the guild from Discord interaction or event envelopes, or independently
authorized Web requests. Bind a trusted `GuildTenant` before accessing guild
state. Reject DMs and unknown guilds at that boundary unless a specific flow
can resolve and authorize its guild from a stored origin. A guild ID supplied
by a caller is never proof of membership or owner authority.

Use the legacy database path as the primary guild binding. Start other guilds
with an initialized SQLite file and an ownership marker for the exact guild.
Store only one accepted binding per guild under a private Host-owned directory.
Reuse the existing schema and repositories. SQLite remains a deployment choice.
A future shared storage backend requires a schema migration and independent
tenant-constraint verification.

Several legacy tables and keys lack a guild discriminator. Per-guild files
are safer than adding predicates to guessed historical ownership or cloning
the reference guild's records. Keep runtime services and in-memory dispatch
shared. Scope locks, cache keys, scheduled claims, resource IDs and restore
operations by verified guild as well as by local identity. A new path alone
does not isolate background work or live Discord API calls.

## Audit and activation gates

| Boundary | Current dependency | Before the cohort can serve live guilds |
| --- | --- | --- |
| Command registration | `bot.py` copies commands to `GUILD_ID`, clears globals, and selects the Event Manager or legacy LFG cogs at process scope | Register an allowlisted per-guild command surface. Gate each interaction, view and modal before invoking callbacks. Resolve legacy vs Skill authority per guild before enabling the feature. |
| Config and SQLite | `settings`, catalog data, managed profile roles and parts of onboarding use legacy keys or single-guild defaults | Use the tenant binding for every DB access, including health and maintenance helpers that read raw paths. Make global environment fallback IDs primary-only. |
| Permissions and resources | Setup already checks actual inventory, role hierarchy, parent permissions and owner confirmation. A Discord channel ID can still be supplied from the wrong guild | Check both resource guild and tenant, then retain existing ownership and private access checks. A fresh guild gets its own mappings. |
| Skills, Event Manager and Progression | Skill lifecycle, scheduling and adapters generally receive guild IDs. Legacy Event Manager cutover and shared Gateway listeners still require process-boundary tests | Route lifecycle, management, event delivery and scheduler execution to the bound guild. Verify authority and independent recovery under Event Manager and Progression fixtures. |
| Background work and caches | Startup, ticket reconciliation, voice, Progression, LFG, and auditing inspect `bot.guilds` or listen to shared events. Some locks and persistent view IDs assume local uniqueness | Enumerate admitted tenants, bind before any work, quarantine failures per tenant, and include guild in mutable process keys. Hold stale callbacks and queued retries after removal. |
| Web | Actor and guild authorization already exist in the Host API. Data access currently resolves against the process database | Derive tenant from the authorized guild server-side and bind before service calls. Recheck the current actor. Keep the private API credential server-side. |

## Lifecycle and rollback

First join checks the exact application, guild membership and bounded admission
policy before provisioning. An owner then chooses a template or Skills through
an ephemeral setup entry point. Every Discord mutation uses its existing
confirmation and authorization. A failed admission provides no default
database access.

Removal suspends live dispatch and jobs, revokes in-memory handles and drops
the runtime admission. Retain the database and Discord resource history.
Rejoin revalidates the application and owner, recovers the same verified
database, and resumes only previously enabled Skills after permission checks.
Orphan review lists resources and data separately. Purge requires its own
owner authorization and verified backup. No implicit resource cleanup.

The first storage implementation can rehearse provisioning, scope, removal
from the offline cohort and rebinding on rejoin. It has no live Gateway
admission or per-tenant job dispatcher. A task that inherits a completed scope
cannot reuse its binding or fall back to the primary file. Long-lived workers
must bind an admitted guild for each operation. The legacy database needs no data
copy or schema change for this milestone. Keep a verified backup before a
live pilot. Rollback restores the prior Host and primary guild binding,
quarantines additional tenant files, and keeps their contents and ownership
metadata available for a reviewed recovery.

Provisioning initializes, seeds, and integrity-checks a private scratch SQLite
file on the same filesystem. The initialized file is published using an
atomic rename that refuses an occupied destination. A retry can ignore
abandoned scratch files and reuse a published database only after verifying its
guild ownership marker. An unknown file at the final path stays in place for
read-only investigation and owner-reviewed recovery. Filesystems without a
supported no-replace rename reject new provisioning. The legacy path is
separate from this operation.

## Protected Staging sequence

1. Record the installed Host, exact application identity and primary guild,
   Skill artifacts and lifecycle, mounts, verified backup, restore probe
   and recovery latch. Rehearse the migration and rollback against a disposable
   baseline. Keep Production separate.
2. Review and test the Gateway, interaction, event, and job admission boundary.
   Prepare immutable Host and controller revisions with an owner-reviewed
   **exact set** containing the original Staging guild and Servertest.
   The owner must approve that change before the pilot. Keep unknown guilds
   outside the cohort even if the application can join them.
3. Replace the original singleton **only in the approved pilot profile**.
   Current verification checks exact singleton membership before protected
   work because it assumes a dedicated bot, mounted database, and resource
   policy. Require the same trusted identity, exact approved membership set,
   profile binding, artifacts, backups, restore, rollback and owner review
   under the new profile. A missing or additional guild fails closed. An invite
   alone conveys no admission.
4. Verify owner-only preview, separate permissions, optional Skill-only setup,
   explicit confirm, repeat IDs, independent jobs and restart recovery. Verify
   original Staging data and Skill ownership both before and after. Resource
   changes in Servertest need their own reviewed preview and approval.
5. Hold public enrollment until removal/rejoin, stale callback rejection,
   backup coverage for every admitted database and retention controls pass.

The [2026-11-19 primary-only recovery acceptance and next pilot plan](../../../deploy/staging/HOST_PROMOTION.md#primary-only-recovery-accepted-on-2026-11-19)
records the active repaired Host/image, retained tenant state and required
protected baseline-generation transition. Future pilot preparation starts from
that accepted runtime after reconciling its policy binding.

## Milestone 2 acceptance scope — 20 November 2026

The pilot is active for the exact approved Staging/Servertest cohort. Platform
safety and customer template completion are different acceptance scopes. A
guild may choose no template, partially apply one, or leave all Skills disabled.
None of those choices permits access to another guild's data or resources.
Once Gaming Community is selected, its missing Core resources remain required
for **that template's** completion; they are not reclassified as optional modules.

Protected read-only collection [44777476448](https://github.com/Duy-Phan96/GamerHQ/actions/runs/44777476448)
has stable inventory, preview, Skill and lifecycle components in both passes,
no foreign/unresolved mappings, both live cross-authorized Host reads denied403,
and all five ingress guard fixtures passing with authenticated live bindings.
Both tenant snapshots passed the existing read-only SQL/integrity contracts;
secondary scope additionally verifies its private file and guild ownership seal.
This is source-backed binding evidence, not a fresh exported UID/GID attestation.

Primary template:55 configured,0 create/review/blocked, journal COMPLETE.
Secondary template:52 configured,10 create,0 review/blocked, journal remaining10,
no unresolved creation checkpoints. Both explicitly mapped Staff roles are
verified with zero guild-wide privileges. The ten pending resources are STAFF,
SUPPORT TICKETS and eight STAFF children: bot-log, games-log, mod-commands,
mod-log, server-log, staff-chat, staff-suggestions and ticket-logs. This is an
expected partial customer installation, not a tenant-routing defect. Completion
still requires a fresh owner-reviewed preview and separate Apply confirmation.

Primary Event Manager package0.1.2 is installed/configured/disabled; its stored
lifecycle version0.1.1 remains unchanged. Secondary has the package available
but no installation/configuration/enablement. Their lifecycle flags agree with
their separate storage. Package availability does not install a Skill into a
guild, and a disabled flag is not proof of a stopped runtime.

The original collector result remains FAIL and primary guild result UNVERIFIED:
the legacy aggregate combines incomplete customer setup with unavailable live
Skill runtime flags. `tools/tenant_acceptance_scope.py` is an offline companion
to the pinned report renderer. It verifies retained raw pass commitments and
known semantic comparison fields, then reports bounded read-only platform
safety separately from COMPLETE/INCOMPLETE/NOT_SELECTED template status. It
preserves original statuses, counts, missing keys and evidence gaps. It has no
SQL, Discord, credentials, deployment or configuration access and changes no
enrolled collector/client/workflow pin. Verify run/artifact provenance before
using this interpretation; it is not an admission, deployment or merge gate.

Remaining coverage is explicit: live callback-registry inspection, full member
role audit, live mutation-path tests and live Skill runtime-running flags are
UNVERIFIED. This collection also does not refresh backups/restores, export OS
file ownership, or accept public enrollment/Production readiness. Existing
offline routing/revocation/persistence/restore/rollback regressions and protected
cutover evidence are complementary, not replacements for those live checks.

Recommendation: after final CI/review, PR317 may merge into **develop for the
bounded two-guild Milestone 2 implementation**. Secondary template completion
is not a prerequisite for that scope. Keep public enrollment, production
deployment and further Discord writes separately gated. Keep the PR draft
until the owner accepts this scope and final review; no automatic merge,
template application, Skill activation or rollout follows this record.

## Milestone 3 — proposed generalized guild onboarding

**State: design/implementation proposal; no public admission or Production authorization.** This phase extends the proven two-guild Staging implementation rather than creating another bot per customer or copying GamerHQ-specific resources. The exact approved Staging cohort remains the only live admission set until a separate protected, owner-reviewed expansion.

### Boundaries and source of truth

1. A versioned **admission registry** is authoritative for the application identity, each guild ID, eligibility, owner consent, admission generation and active/suspended/removal status. Never infer admission from Discord membership, supplied IDs, a role, template, or previous guild cache. Preserve the historical exact-cohort policy and incident journal as immutable audit evidence.
2. **Tenant provisioning** creates one private, ownership-sealed SQLite database per new guild, reusing the current transactional/atomic no-replace path with idempotent resume. The original primary database must remain at its legacy path and must not be cloned to seed another customer. Per-tenant storage paths, scheduler claims, cache keys, locks and Discord resource mappings must be scoped to the bound guild.
3. **Installation choice is separate from admission:** after verified owner authorization, a guild can choose (a) no template, (b) Gaming Community, or (c) Skills only. Template preview, per-resource permissions and confirm/Apply remain required before any Discord write. A Skill's package availability is not its installation, configuration or enabled state.
4. All interaction ingress, Web APIs, component/modal callbacks and queued/scheduled work must revalidate actor, guild admission and current lifecycle generation. Expired or removed guilds fail closed; DMs and unknown guilds cannot fall through to the primary database. Discord is an integration/view, not the authority for saved application state.
5. Admission must not silently grant Administrator, create Staff resources, enable Skills, send messages or deploy a new Host image. Sensitive changes require scoped permission checks, previews, audit records and explicit owner confirmation.

### Proposed delivery slices and evidence gates

| Slice | Implement only after | Acceptance evidence |
| --- | --- | --- |
| V3.1 Registry contract + migration plan | Audit current two-guild binding, existing state and recovery journal | Versioned schema, deterministic migration rehearsal, exact-cohort compatibility, negative unauthorized-admission tests; **no live admission change** |
| V3.2 Provision/admit one disposable third Staging guild | Owner-reviewed deployment plan, permissions, dual-guild rollback preservation | Guild-specific DB seal, idempotent retries, command/Web/Skill/scheduler isolation, permission and negative cross-tenant tests |
| V3.3 Self-service owner journey | V3.2 verified and independently authorized | Owner identity verification, no-template/Skill-only/template paths, dry-run/Preview/Confirm, stable repeated resource IDs and reports |
| V3.4 Lifecycle and operational scaling | Rejoin/removal protocol and per-guild backup/restore contracts | Safe revoke/rejoin, stale callbacks and delayed tasks denied, scoped health and observable errors, backup/restore and failure-injection tests |
| V3.5 Wider rollout readiness | Separate Production review and all earlier evidence | Load/rate-limit behavior, live command registry, member-role audit, Skill runtime flags, OS ownership, backup attestation, rollback, and release approval |

Do not implement a hardcoded third-guild exception as a substitute for registry design. The existing `primaryOnly` recovery path remains available throughout the transition. Failures must preserve checkpoints, resource IDs, guild-specific journal state, original member data and the ability to return to the healthy two-guild baseline. No public enrollment or Production release is implied by passing offline tests.

### Current known limitations and workflow ownership

- Servertest's ten missing Gaming Community resources are an **incomplete customer template**, not a tenant-isolation defect. No automatic setup/repair is authorized here.
- Milestone 2 scope-based read-only acceptance passed platform safety, but did not attest all live mutation paths, callback registry, full membership-role inventory, fresh OS ownership/backup status, public enrollment or Production suitability.
- `main` is the stable default/release branch and `develop` is the feature integration branch. Both have PR requirements and required CI checks; review approvals are currently zero for solo maintenance. Environment-level owner approval and OIDC restrictions stay independent of branch protection.
- Any planned change to admission policy, encrypted credentials, runtime deployments or Discord resources has its own protected review, rollback and audit trail; documentation or a GitHub PR merge does not authorize it.
