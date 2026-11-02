# Event Manager implementation and authority cutover

Continuation of owner request / issue #172; existing PRs #173, #174, #175,
Skill #1 and Web #66 are reused. All original heads were green, mergeable and
had no submitted reviews or unresolved review threads at the start of this work.

## Public boundary

SDK 0.3 candidate now exposes namespace-scoped `storage.transact(checks, writes)`
under `storage.atomic`, and `SkillContext.resources` under
`discord.resources.manage`. Transactions compare all expectations before any
write and cannot address another tenant/Skill. Limits are 10,000 keys, 64 KiB
per value and 16 MiB per transaction. Null means absent/delete, not JSON null.

Managed resources accept logical keys, participant IDs, text/voice configuration
and overview data. No raw Discord client, arbitrary channel ID or role management
is part of this contract. Trusted Host policy is stored at
`skill_resources:<guild-id>:<skill-id>` with `categoryId`, `allowPublic` (default
false), and `maxResources` (default 100). Configuration is an explicit Host action;
missing policy or inadequate category permissions fails closed. No deployed
policy or Discord permission was changed during development.

Resource identity/ownership, leases and message recovery are persisted in the
existing SQLite database. One stable key owns one text area/card. Text areas have
no delete operation. Retries use authoritative Discord inventory and stored
ownership markers. Unknown creation/publication outcomes stop for reconciliation
rather than blindly creating another resource. Participant removal restores only
the permission bits the Host adapter managed. Temporary voice deletion is limited
to owned empty channels; occupied sessions remain.

## Verified migration implementation

The installed Skill owns the versioned `event-manager.migrate-plan.v1` transform.
All six V1 tables are preserved row-for-row under `legacy.v1:*`; native Events,
occurrences and stable legacy-ID aliases are produced by the Skill. Per-occurrence
membership/history and voice/message/share/proposal references remain distinct.
Finite legacy recurrence/end policies remain in force until explicit conversion.
No channel deletion or arbitrary private-to-public conversion is allowed.

`hosts/gamerhq/event_cutover.py` provides trusted compatibility orchestration:
prepare via the normal management router; verify a distinct SQLite backup by hash
and integrity; verify selected legacy channel/message ownership; lock the source;
recheck its hash; validate namespace/size bounds; import and switch authority in
one transaction. A source change, namespace conflict or mismatched backup commits
nothing. Import retry is idempotent. Legacy tables remain as frozen rollback data.

The authority row installs SQLite write barriers on every operation in all six
legacy tables for the cut-over guild. Unrelated tenants remain writable. Legacy
scheduled-row queries exclude Skill-owned guilds. Runtime mutation/enablement
refuses to establish a second domain while unmigrated legacy data exists.

Rollback only accepts an unchanged imported namespace with the Skill disabled.
Adopted resource mappings require explicit external verification and unchanged
baseline hashes. Any new V2 data or resource effect blocks automatic rollback;
a reviewed reverse migration or matched application/database restoration is then
required. Code rollback does not restore database authority.

## Evidence and deployment boundary

Offline tests exercise actual installed Skill plans locally, with realistic
synthetic six-table SQLite fixtures, transactional source-change/backup/conflict
failures, legacy write barriers, tenant isolation and rollback refusal after V2
writes. Host CI can validate the recorded public response fixture without access
to the private Skill repository; Skill CI independently validates its transform.
Managed-resource tests cover retry/restart identity, permission revocation,
ambiguous ownership and uncertain remote effects.

No live database, Gateway, channel or credential was used. This implementation
does not authorize production cutover. Release must include reviewed package
access, complete adapter integration, an isolated staging rehearsal, an exact
image/backup candidate and explicit production approval. Interrupted durable
effect claims intentionally stop mutations until the Host verifies the old
worker is quiescent and reconciles remote state; they are not silently cleared.
