# ADR-001 Guild tenancy for the shared GamerHQ application

Status: Proposed for implementation review

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
