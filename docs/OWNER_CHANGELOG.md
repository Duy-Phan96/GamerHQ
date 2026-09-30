# Owner Change Log

Open `/server manage` → **Owner Change Log** → **Confirm Owner Log** as the current guild owner. This explicitly creates or repairs `🕘・owner-changelog` inside the existing private STAFF category. Startup never creates a replacement channel.

## Automatic messages, not just a launcher

Each new change recorded by the existing Discord structure observer gets an individual message in the configured channel. Notices show a generic change type, timestamp, record number and status, with **Undo** and **Details** buttons. This covers the existing mapped channel/category changes, managed-message removals, restores and security-review records. It does not claim to be a complete Discord audit log of untracked resources or every feature's domain action.

The existing pinned **Review / Undo** entry remains for earlier history. On first feed activation, historical records are not bulk-posted. Existing configured channels enable the feed after startup; new installations enable it on confirmed setup. New observations publish through the existing structure announcement path. A bounded 60-second recovery loop picks up records awaiting delivery without scanning guild inventories or message histories.

Click **Undo** on a notice to open its before/after review as an owner-only ephemeral response. Nothing changes until **Confirm Undo** is clicked. Confirmation is actor/guild-bound, expires after two minutes, is single-use and rechecks the current owner and saved change. The existing Undo service also rejects conflicting later channel edits. **Cancel** leaves the server untouched. Shared persistent handlers resolve the saved channel/message/change binding from SQLite, so notices remain usable after bot restarts.

After Undo or replacement, the original notification is updated in place to show **Undone** or **Replacement restored**, and its Undo button is disabled. It is not posted again. Irreversible deletions have disabled Undo; **Details** may offer a separately confirmed replacement where the existing restore service supports it. Deleted channel history cannot be recovered. Offline-reconciliation or unsupported changes do not advertise an Undo the service cannot execute.

## Permissions and privacy

The feature uses at most three channel overwrites: `@everyone` denied, the cached owner allowed to read/use controls, and GamerHQ allowed to publish/manage messages. The owner has intrinsic guild access even when absent from the member cache. There is no deny for every game/server role.

Confirmed repair replaces only the mapped Owner Change Log channel's access list. Obsolete role/member grants are removed there, not from STAFF, other channels, guild roles, games or member selections. The child does not sync STAFF permissions. Publication stops if an unrelated explicit view grant is present or the destination cannot be validated.

**Discord Administrator bypasses channel denies.** Administrators can see the launcher and generic notice metadata. Channel messages never contain actor identities, affected resource names/IDs, before/after snapshots, tokens or private content. All detailed history and Undo/restore actions require the current owner and are sent ephemerally. This extends the previous launcher-only model without exposing its private details. The general server-log retains its separate staff-facing policy.

See [permission contracts](PERMISSIONS.md) and [Discord's permission reference](https://docs.discord.com/developers/topics/permissions).

## Identity, delivery and safe retries

The existing `structure_change_log` remains authoritative. Per-guild feed cursors, delivery reservations and message-to-change bindings use namespaced entries in the existing `settings` table; no new database schema or replacement is required.

Repeated announcements and reconnects do not send duplicate notices. Delivery reserves a record before sending. A definite rejection can be retried; uncertain delivery or a crash during sending keeps a reservation and does not automatically resend. The complete change is still accessible from the private history. A failed terminal-status edit is retried by saved message ID. A deleted notice is never automatically recreated.

The recovery loop handles at most ten new records and ten changed terminal notices per guild per pass. It does not fetch a full guild inventory or scan channel history. Notification failure must not roll back an adopted configuration change.

Setup confirmations expire after two minutes and are single-use. A guild-level lock and stored-ID recheck prevent duplicate channel creation. Unknown same-name channels are not adopted; stale stored IDs require review. Returned channel objects are used before gateway cache convergence. Definite setup 400/403 rejection permits a fresh reviewed retry; uncertain creation stays reserved for review.

## Verification and rollout

`tests/test_owner_change_feed.py` covers individual messages, duplicate suppression, persisted bindings across restart, direct owner-private review, single-use/expired/stale confirmation, irreversible changes, safe retries, status edits, missing notices, bounded delivery and privacy checks. Existing `tests/test_owner_changelog.py` and `tests/test_structure_adoption.py` remain enabled.

After a reviewed deployment, test by renaming one harmless already-mapped channel. Confirm its notice appears without opening history, click **Undo**, inspect the private before/after review and confirm. Verify the channel name is restored and the same notice changes to **Undone**. Also test that a non-owner Administrator cannot open details or confirm anything. Offline tests do not replace this live acceptance step.
