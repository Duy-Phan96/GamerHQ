# Owner Change Log

Open `/server manage` → **Owner Change Log** → **Confirm Owner Log** as the current guild owner. This explicitly creates or repairs `🕘・owner-changelog` inside the existing private STAFF category. Startup never creates a replacement channel. An already configured channel needs no setup command to receive new changes.

## Automatic messages, not just a launcher

Each new supported server configuration change gets an individual message in the configured channel, with a generic change type, timestamp, record number and status. **Undo** opens that exact change privately; **Details** remains available when automatic Undo is unsafe. The pinned **Review / Undo** entry still opens earlier history.

One narrow exception reduces category-deletion noise: when one category deletion and at least two pure child-channel parent moves are recorded for the same guild within a four-second window, with no unrelated change interleaved, the feed may publish one grouped notice such as `Category removed · 3 related channel moves`. The original history rows are not deleted, rewritten or merged. Owner-only Details lists the affected channels. Any rename, permission/content mutation, second nearby category deletion, prior/uncertain delivery, or ambiguous ordering falls back to normal individual notices.

`cogs.server_changes` now observes channel/category creation, updates and deletion, role creation/updates/deletion, and guild settings updates. This includes channels not managed by GamerHQ and changes made manually, by other bots or by GamerHQ. Normal messages, reactions, voice joins and member-role assignments are not server configuration changes tracked by this observer. Thread lifecycle and every Discord setting are not a complete audit-log export.

`services.server_change_observer` adapts those Gateway events into the existing `structure_change_log` and `owner_change_feed`. It does not create a second history, notification channel, resource manager or database schema. It records configured channel fields (including permission overwrites), role settings/permissions, and supported guild settings. New generic records use **Unknown** for the actor rather than guessing from an unrelated audit entry. Existing managed-layout records retain their existing audit attribution.

Managed channel/category layout and deletion handling stays with the existing structure service. The adapter records only fields not already covered by that event's legacy entry, and also captures bot writes that the legacy expected-change filter suppresses. The existing strict managed-permission security review/repair path remains unchanged. Observing an untracked resource does not adopt it into GamerHQ management or change its permissions.

## Undo scope and confirmation

Click **Undo** to open the before/after review as an owner-only ephemeral response. Nothing changes until **Confirm Undo** is clicked. Confirmation is guild/owner-bound, expires after two minutes, is single-use and rechecks the current owner and saved change. **Cancel** leaves the server untouched. Persistent handlers resolve the channel/message/change binding from SQLite after restarts.

For newly observed updates, automatic Undo is deliberately limited to:

- Channel name, topic, NSFW flag, slow mode and supported voice bitrate/user limit/region; category name.
- Editable role name, colour, hoist and mentionability, subject to bot permission and role hierarchy checks.
- Server name, description and AFK timeout.

All changed fields in an individual entry must be supported before it advertises Undo. Permission changes, ordering/placement, creations, deletions, other settings and offline net differences remain recorded for private manual review, not an unsafe automatic reversal. Existing mapped-layout Undo and separately confirmed replacement features remain available under their original rules.

The new Undo path fetches current Discord state instead of trusting the cache, rejects conflicts in the affected fields, edits only those fields and verifies the result with a second fetch. Unrelated newer fields are preserved. The actual current owner is checked through a fresh guild read; roles require Manage Roles and a higher bot role. Discord may still reject changes because permissions or ownership changed concurrently.

A durable `UNDOING` reservation prevents concurrent execution. A partial, interrupted or unverified remote write becomes `REVIEW_REQUIRED`, never a false success or a blind retry. After verified success, the existing notice is updated to **Undone** and Undo is disabled. The observer saves the verified state so the Undo event does not create a recursive notice. A confirmed mapped display-name Undo updates the existing runtime display state without overwriting unrelated desired fields.

Deletion cannot recover original IDs or message history. The existing restore service may offer a separately confirmed replacement for supported managed resources; newly observed untracked deletions do not promise restoration.

## Startup, reconnect and delivery

The bot already loads both cogs and uses the guild Gateway intent through `discord.Intents.default()`. No extra privileged Message Content intent is required for these configuration events. Existing audit attribution needs View Audit Log; generic change detection does not depend on identifying the actor. Undo requires the relevant current Discord manage permissions.

Observation snapshots are namespaced `owner_observation:*` settings, separate from managed desired/runtime state. The first baseline is quiet. Ready/resume reconciles cached channels, roles and guild settings with persisted observations; later differences are explicitly non-reversible offline net differences, not invented intermediate events. Unavailable guilds are skipped, and an absent cached resource is not asserted to be a proven deletion. Existing mapped-layout bootstrap retains its own compatibility behavior.

The feed cursor is initialized before recording new observations, even when the destination is temporarily unavailable, so later delivery does not skip that initial backlog. New events attempt delivery immediately without a manual command. The existing bounded 60-second feed recovery retries definite delivery failures and pending status edits. Inventory reconciliation runs on ready/resume/join, not in that periodic delivery loop.

Delivery reserves a record before sending. A definite rejection can be retried; an uncertain HTTP outcome or a crash during sending retains a reservation and does not automatically resend. The complete change remains accessible in private history. Terminal-status edits target the saved message ID. Deleted notices are never automatically recreated. Each recovery pass handles at most ten new records and ten dirty notices per guild, without scanning message history.

## Permissions and privacy

The destination has at most three explicit channel overwrites: `@everyone` denied, the cached owner allowed to read/use controls, and GamerHQ allowed to publish/manage messages. No deny is added for every server/game role. The owner has intrinsic guild access even when absent from the member cache.

Confirmed repair replaces only the mapped Owner Change Log channel's access list, not STAFF or unrelated channels/roles. The child does not sync STAFF permissions. Publication stops if an unrelated explicit view grant is present or the destination cannot be validated. Missing or stale mappings are not replaced automatically, and same-name channels are not proof of ownership.

**Discord Administrator bypasses channel denies.** Administrators can see the generic launcher and notice metadata. Resource names/IDs, actors, before/after values, tokens and private content never go into those ordinary channel messages. Detailed history and Undo/restore actions require the current real owner, not an Owner-named role or Administrator. The separate staff-facing server-log retains its existing policy. See [permission contracts](PERMISSIONS.md).

Setup confirmation expires after two minutes and is single-use. A guild lock and stored-ID recheck prevent duplicate creation. Returned channel objects are used before Gateway cache convergence. Definite setup 400/403 rejection permits a fresh reviewed retry; uncertain creation stays reserved for review.

## Verification and rollout

Run offline from repository root:

```bash
python -m pytest tests/test_server_change_observer.py tests/test_owner_change_feed.py tests/test_owner_changelog.py tests/test_structure_adoption.py
python -m pytest
```

The new regression suite covers untracked/manual/bot changes, role/guild events, duplicate and restart handling, initial/backlog/offline behavior, owner/permission checks, stale and concurrent Undo, fresh-state verification, partial failures, hierarchy and managed desired-state consistency. Existing notification persistence and privacy tests remain enabled. Offline tests do not constitute live Discord acceptance.

After a reviewed deployment, create a harmless **unmapped** test channel and verify its creation notice. Rename it; the next notice must offer Undo without opening history first. Review and confirm the rename, verify the old name returns and the same notice becomes Undone. Repeat with a harmless editable role name, then verify a permission change is recorded with Undo unavailable. Test a non-owner Administrator, a conflicting later rename and a bot restart with an old notice. Do not test with critical/private access settings or delete real content.

This change is based on `feature/game-independent-events`, which contains the existing Owner Change Log. An older `main` checkout does not gain it merely by pulling main; review the feature/fix merge before the owner's production update. The implementation does not deploy, start a live bot or replace production data.
