# Runtime database migration and reconciliation

Git contains application code, not Discord identity or community runtime state.
The Windows DB and VPS DB are separate stores. Starting against a fresh DB loses
knowledge of existing resources; Discord messages do not disappear. Historically,
Giveaways had no recovery predicate and a missing message ID caused another post.

## What must migrate

Normally transfer the **whole verified SQLite snapshot**, not selected ID rows.
The schema audit below describes tables only; no live database content was read.

| Tables | Persistent state |
| --- | --- |
| `settings` | Channel/category/message/pin IDs, layout/adoption, desired-state approvals, migration journals, cooldowns and feature configuration |
| `managed_roles` | Base/profile/LFG role IDs and stable keys |
| `managed_message_content`, `managed_message_audit` | Customized text/buttons, identity, hashes, delivery state, versions and audit |
| `games`, `deleted_games` | Catalog choices, roles, optional area/category/channel IDs and deletion tombstones |
| `lfg_events`, `lfg_event_members`, `lfg_event_messages`, `lfg_voice_notifications`, `lfg_time_proposals` | Public/private sessions, invitations/share tokens, participants, cards, voice and proposals |
| `support_tickets`, `ticket_audit` | Private ticket content, ownership, channels/messages, state and actions |
| `suggestions` | Private submissions, review state and Staff-message IDs |
| `temp_voice_channels` | Temporary room ownership and game mapping |
| `curated_deals`, `processed_affiliate_deals` | Posted/reserved/uncertain delivery claims and source/response mappings; preserve to prevent reposting |
| `twitch_connections`, `twitch_live_deliveries` | Private OAuth credentials, accounts and notification claims |
| `streamer_applications`, `streamer_profiles`, `streamer_channels` | Legacy/beta approvals, profiles, follower roles and channels |

Protect snapshots like credentials. Keep them out of Git, reports and public
chat. Copy `.env` separately and privately as needed; the import never changes it.

**Divergent databases are not merged.** If tickets, LFG sessions, deals or other
state were created on the VPS after cutover, replacing its DB with the old local
snapshot would discard that newer state. Retain both snapshots and choose the
authoritative store before applying an import. Prefer reconciling the current
VPS DB if it holds newer activity. Discord adoption cannot reconstruct lost
private ticket content, sessions, OAuth credentials or delivery claims.

## Safe reconciliation

Run `/server health`, then `/server reconcile`. Existing mappings are preferred; a single confident candidate can be linked on confirmation, while multiple candidates open an owner/admin choice. No Discord resource is edited or deleted by reconciliation. Rescan after linking channels, then review `/server message-duplicates` and `/server repair`. Use owner `/server setup` only for genuinely missing resources. See the [canonical operations guide](PRODUCTION_OPERATIONS.md).

Optional game areas and their roles retain the existing `/game-admin recover-existing` workflow; arbitrary resources cannot be inferred from names alone.

The legacy shared fixed-message helper checks at most 100 pins and 100 recent
history messages in the expected channel, with an extra item to detect truncation.
Reconciliation previews validate mapped messages by exact ID without history;
explicit duplicate review still inspects the bounded channel snapshot.
Only this GamerHQ bot's messages with exact canonical content or
an owning feature's known legacy fingerprint qualify. It reuses one match, persists
its ID and updates in place. A missing recovery callback now still gets exact-body
recovery (including Giveaways). Multiple candidates stop the update, even if one
has a stored ID. An incomplete history scan blocks adoption/creation; a validated
stored message can still update in a busy channel. Failed API inspection blocks
writes. Import the original DB/review the channel instead of assuming absence. Custom
managed content/buttons remain authoritative. Unknown/manual posts are preserved.

Normal Games selector refresh also reports duplicate candidate messages instead
of deleting them. Existing explicit rebuild/legacy-retirement operations retain
their separate confirmations and ownership checks. Reconciliation is not a bulk
cleanup or a way to infer ownership from a name alone.

Health is read-only: it reports the active runtime DB path, missing/stale mappings,
adoption opportunities and duplicate canonical messages/channels/roles. Detailed
message keys identify cleanup targets. Discord inspection is bounded; a warning
is not permission to repost or delete an unknown message.

## Confirmed duplicate cleanup

Run without arguments to audit every active canonical board in the managed-message
registry, including LFG, Giveaways, welcome/guide, role panels, game selectors,
Support/Marketplace messages, command guides and the managed Streamer Hub guide:

```text
/server message-duplicates
```

The read-only, paginated summary includes unique boards and duplicate groups in
their expected channels. **Review Duplicates** opens each group; **Close** changes
nothing. To restrict the scan, use the optional existing key:

```text
/server message-duplicates managed_key:server_future_giveaways_message_id
```

Only the current guild owner/admin can open and confirm the three-minute session.
Each pair shows channel, message IDs/links, creation/edit dates, current DB mapping
and content previews. The recommendation prefers a valid persisted mapping, then
exact canonical content with matching controls, then the older original when only
line endings differ. An age tie-break never overrides a valid mapping. Choosing
**Keep A / Remove B** or **Keep B / Remove A** explicitly confirms that single
removal. **Skip** leaves the group unchanged; **Cancel** ends the remaining review.
Earlier confirmed removals are not undone. Groups larger than two require another
fresh pair confirmation for every removal; there is no bulk delete.

Only GamerHQ-authored messages with the canonical body (or stored customized body)
in the expected channel qualify. CRLF/LF and trailing newlines may differ; similar
headings/prose alone never qualify. Unknown/manual and unrecognized legacy messages
are ignored. Differing buttons, embeds, attachments, unresolved customization and
incomplete history scans require manual review. Inactive legacy streamer directories, event posts and private
ticket records are not canonical boards and are never swept as duplicates.

Before removal the bot re-fetches both messages and rechecks their channel,
authors/fingerprints, current authorization, unchanged group/mappings/custom state
and all other runtime references (including JSON). It saves the selected ID and
customization metadata first, then deletes only the confirmed other message.
Game section IDs update their existing JSON slot without replacing other slots.
External references block cleanup rather than being erased. The retained message
is not edited: its ID, body, buttons and pin stay intact; normal feature refresh
continues to use it and recover its persistent controls. Audit intent and successful
cleanup are logged without message content. Failed/uncertain deletion requires a
fresh review, never automatic retry.

Health reports the number of duplicate groups and the global command without
mutating messages. Setup Repair adopts a single valid candidate, reports multiple
candidates for review and never posts a third. LFG Repair uses this same helper.
After review run `/server health`, then `/server reconcile`, then `/server repair` (preview and confirm), and repeat the
global audit to verify that the repaired mapping remains stable.

## Transfer and validate (owner operations only)

Run **one active GamerHQ process per live guild**, even when the processes use
different DB files. Local + VPS processes compete on event handling, messages and
persistent views. In-process locks do not coordinate across machines. Startup
prints this warning; there is no distributed instance lock.

1. Stop the local bot with Ctrl+C and stop the VPS bot. Coordinate the maintenance
   window: disable any automatic/manual restarts until finished.
2. Back up both installations using SQLite backup, never a live plain file copy.
3. Transfer the local snapshot to a *new* file in the VPS backups directory.
4. Rehearse compatibility/migrations, then explicitly import only if the chosen
   source is authoritative. Import creates a new verified backup of the current
   VPS DB before atomic replacement. Never copy over the live target directly.
5. Start one VPS bot, inspect health, then preview setup. Review duplicates
   separately; do not repeatedly press Repair to try to remove them.

Windows, from the checkout with the local bot stopped:

```powershell
$transfer = Join-Path $env:USERPROFILE ('GamerHQ-Transfer-' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $transfer
.\.venv\Scripts\python.exe -m tools.backup_database "$transfer\local-runtime.db"
$destination = Read-Host 'SSH destination (user@host) for the GamerHQ VPS'
scp "$transfer\local-runtime.db" "${destination}:/opt/gamerhq/backups/local-runtime.db"
```

Use a new remote filename if `local-runtime.db` already exists. These example
paths follow [DEPLOY.md](../DEPLOY.md); the tool accepts arbitrary explicit paths.
The source snapshot must be readable by the container UID 10001 and mode 600.

VPS, using the reviewed image containing the import tool, with no running bot:

```bash
cd /opt/gamerhq/app
docker compose stop gamerhq
docker compose build gamerhq
docker compose run --rm --no-deps --entrypoint python gamerhq -m tools.import_database \
  --source /app/backups/local-runtime.db --target /app/runtime/data/gamerhq.db
```

The default mode only migrates a temporary copy. Historical databases have
`PRAGMA user_version=0`; current schema version is 1. Unknown future versions,
missing core columns/tables, corrupt files and incomplete migrations are rejected.
Normal additive application migrations run on the copy; all runtime tables and
unknown extra tables survive. Catalog seeding is not run by the import.

After reviewing which store to keep, explicitly replace the production DB:

```bash
backup="/app/backups/pre-import-$(date -u +%Y%m%dT%H%M%SZ).db"
docker compose run --rm --no-deps --entrypoint python gamerhq -m tools.import_database \
  --source /app/backups/local-runtime.db --target /app/runtime/data/gamerhq.db \
  --backup "$backup" --bots-stopped --apply
docker compose up -d --wait gamerhq
docker compose ps
```

`--bots-stopped` is the owner's shutdown attestation, not a cross-machine lock.
The target must exist and the backup path must be new. Leftover SQLite sidecars
block replacement; do not delete them blindly. Source/target and backup cannot
alias. If validation/import fails, do not run the start commands until reviewed.
For a target that does not yet exist, follow the first-install procedure in
[DEPLOY.md](../DEPLOY.md) using a verified snapshot.

Discord after startup:

```text
/server health
/server setup
```

Review the preview before Repair. For duplicates use the separate command above.
Keep the pre-import snapshot for recovery; see [rollback](../ROLLBACK.md). Do not
roll back over newer production activity without explicitly accepting its loss.
