# Games: personal selection and visibility-linked text channels

SQLite is authoritative. The public seed supplies starter metadata; it does not replace owner selection, Discord IDs or deletion tombstones.

## Independent state

- `active` + `selectable` + a nonzero `role_id` make a game available in the personal selector. Explicit admin Show/Hide now also reviews the associated channel policy; flags and channel identity remain separate persisted fields.
- `channel_id` is nullable for imported or not-yet-provisioned games. Confirmed Show ensures one text channel under **🎮 Games**; Hide retains the channel as staff-accessible storage. The member role selector never creates a channel.
- Notification roles remain separate opt-ins. Selecting a game never enables pings.
- Legacy area/category/chat/LFG/create-voice columns are migration evidence only. New multi-channel area creation is disabled.

Choose Your Games retains Select Games, Suggest Game, `/game select` and `/game suggest`. The public board carries no personal state. Select Games opens an ephemeral actor-bound panel with green ✅ selected / neutral ➕ add game buttons; clicking one immediately updates that game role and preserves the current range/page. No Save is required. The quick slash command retains its existing confirmation.

Popular shows the current top 25 by cached role-member counts, descending with alphabetical ties. These games also appear in the complete A–Z list. Popular opens by default with 20 game buttons per page (25 games across two pages). Browse A–Z offers nonempty A–E, F–J, K–O, P–T and U–Z ranges, plus 0–9 / Other for remaining names. Game buttons and navigation stay within Discord's 25-component limit; no game dropdown or Save step is needed. A missing role produces a friendly temporary-unavailability message and a private diagnostic; missing mappings are not presented as an empty library. Opening uses one catalog query and one pass over cached members, no Discord inventory/history/member download. Each change refreshes only that member for authorization and preserves unrelated roles. Reopening recomputes Popular.

## Administration

Server Management → Games contains Library, Channels, Candidates, Create/Remove and Suggested Games guidance. Library Show Game and `/game-admin set-visible` share an actor-bound preview and confirmation. Show creates/reuses the game role and Games text channel; Hide removes selection and hides the chat from game members while preserving identity, messages and membership. New approved games use `/game-admin create`; creation/recovery with `visible=true` first stores the library entry as hidden, then opens the same confirmation. Cancelling that review leaves the entry and role stored but hidden. Suggestions retain their existing staff inbox.

Defaults in `config.py`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `GAME_CHANNEL_MEMBER_THRESHOLD` | 10 | Candidate threshold, never automatic creation |
| `GAME_CHANNEL_SOFT_LIMIT` | 20 | Initial operational limit; explicit Create Anyway allowed |
| `POPULAR_GAMES_COUNT` | 25 | Maximum Popular shortcuts; independent of channel count |

After a successful game-role grant, only that game's count is checked. At the threshold a private STAFF **🎮・games-log** candidate is reserved in SQLite before sending. `game_channel_candidates` stores guild/game, timestamp, message ID and PENDING/CREATED/IGNORED state. Repeated grants and restarts do not post it again. An uncertain delivery stays reserved and is available in Games → Candidates; staff can decide there without reposting. Ignore suppresses immediate re-notification.

Creating manually works below threshold. Every creation/migration/removal opens an actor-bound expiring preview and confirmation. Permission, stored IDs and resource signatures are rechecked; simultaneous submissions serialize. A durable creation reservation blocks retries after uncertain delivery: inspect Discord and the stored state privately rather than clearing it blindly. Missing/stale stored channel IDs require review and are never replaced by guessing names.

Channels use normalized names and alphabetical ordering within the shared category, reusing the absolute positions already occupied by game channels rather than resetting them to server-wide zero-based positions. Everyone is denied visibility; the game role can read and chat; staff and GamerHQ retain access. Repair uses stored channel IDs and scoped permissions; no new per-game category, LFG or create-voice resource is generated. Other channels retain their relative order.

Removal only targets the persisted managed channel after dependency checks and explicit confirmation. Discord channel history is deleted with the channel as the preview warns; the game, role, selection and stored event records remain. Export/archive wanted Discord history before confirming. No routine test or startup deletes live channels. Games-log records administrative actions; high-level channel changes also use server-log. Normal member toggles do not spam either log.

## Legacy migration

Owner Server Dev → Legacy Game Migration lists recorded legacy resources. Preview Migration prefers the existing stored chat ID, moves/renames that channel into GAMING, applies game-role access and persists `channel_id`. History and IDs survive. Old resource IDs are retained in `game_legacy_hints`; old categories, LFG and create-voice resources remain untouched for separate manual review. Never infer ownership of an arbitrary channel from its name. Empty old categories may be removed only through a separately reviewed owner cleanup.

Legacy `/area manage` removal and dependency checks remain; its creation backend now refuses new multi-channel areas. Existing centralized LFG and temporary voice systems remain functional during migration.

For an empty production selector, use the [games-scope recovery dry-run](DATABASE_MIGRATION.md#games-scope-recovery-on-copies). Recover catalog metadata, flags and roles; old areas become hints, never active canonical channels. Production-only fields, settings, runtime tables and already adopted role/channel IDs remain authoritative.

## Implementation and validation

`game_selector`, `game_catalog_service`, `game_channel_service`, the `game_visibility_service` orchestration and the existing role/managed-server services own this lifecycle. Regression coverage includes `test_game_channels_v2.py`, role/security tests and the existing LFG/voice suites. Health is read-only and checks shared category, games-log, stored channels, access, ordering and legacy review evidence. Offline tests are not live Discord acceptance.


## Confirmed visibility workflow

`/game-admin set-visible game:<name> visible:true` and Library → **Show Game**
open the same review; neither mutates the library or Discord just by opening it.
**Confirm Show** creates the shared category when absent, or reuses its saved ID.
A unique unlinked Games/Gaming candidate is explicitly presented for linking;
multiple candidates, conflicting mappings and deliberately removed destinations
require owner review rather than an automatic duplicate. An existing Games/Gaming
category is renamed to `🎮 Games` after confirmation without rewriting its rights.

A saved `channel_id` takes precedence over `chat_channel_id` and legacy hints.
Existing chats are moved/renamed, not replaced. A chat whose old category was
already deleted can be migrated with `category_id=None`, after current identity,
ownership, references and overwrite checks. An already-linked chat at the wrong
location is repaired through Show as well. Unknown explicit access grants, stale
IDs and shared ownership block the operation with an explanation. No old category,
LFG/voice channel or chat history is deleted by this workflow.

**Confirm Hide** removes the selection and denies the game-role access to its
chat; the owner, Discord administrators, recognized staff roles and GamerHQ can
still see it. Hide is not permanent deletion. Showing it again reuses the same
ID and message history. A saved hide-policy setting makes the existing repair
policy keep it hidden. Unrelated permission bits and member-specific denies are
preserved; existing repair enforcement still removes unexpected positive grants.
The existing explicitly confirmed Remove Game Channel action is separate.

The administrator needs current administrator/owner access; the bot needs Manage
Channels and Manage Roles and a safe game role below its own. Every confirmation
re-reads channel/role inventory and validates the reviewed fields. Creator-returned
IDs are saved before further awaits, independent of gateway-cache convergence.
Durable reservations prevent duplicate creation after unknown HTTP outcomes.
Known 400/403 rejections can be reviewed and retried; uncertain creations require
manual review. The request is marked PENDING before a remote channel mutation,
and DONE only after placement/rights verification. A failure can leave the desired
visibility saved with a pending operation; reopen the review rather than resetting
IDs, deleting content or overwriting the production database.

### Existing visible games / three-game pilot

Open `/server manage` → **Games** → **Set up Games Channels**. The preview presents
up to three not-ready, currently visible active games plus blocked reasons. Each
button uses a fresh instance of the same Show confirmation. This deliberately
avoids stale multi-game previews after the first action creates the category.
**Refresh / Next games** recomputes the next three; already-ready games are skipped.
Games with an unset role mapping are not silently omitted: their safe role creation
is reviewed; missing previously linked roles are reported as blocked.

No member threshold is required for explicit Show. New channel creation above the
configured soft limit requires **Create Anyway**. Capacity checks include hidden
channels, category capacity and total server channels; no overflow category is
created implicitly. The workflow never starts during bot startup or Health, and
normal member game selection remains a role-only operation.

For the pilot, review three real visible games; confirm each, then refresh and
verify that no duplicate category/chat appears. Verify a moved chat keeps its ID
and messages. Hide/show one game and test with a non-staff member with its role;
using an owner/admin account cannot demonstrate member visibility. No live setup
is implied by a commit, build or offline test result.

### Focused offline checks

```bash
python -m pytest tests/test_game_visibility.py tests/test_game_channels_v2.py tests/test_game_system_migration.py tests/test_stability.py
```

The suite covers missing/shared destinations, creation/migration, detached legacy
chats, role safety, preserved history, repeated/concurrent use, stale reviews,
hide/show/repair persistence, REST versus cache state, uncertain writes, explicit
limits, pilot selection and slash/library delegation. Full repository CI remains
a separate release gate. Deployment and Discord acceptance remain owner-controlled.

## Admin multi-game visibility selector

`/server manage` → **Games** → **Manage Visible Games** opens a private,
actor-bound administrative selector. It reuses the personal selector's catalog
ranking and Popular/A–Z ranges, but never calls member-role selection actions.
All active games, including hidden ones, are browsable. Fifteen game buttons per
page leave room for navigation and draft controls. Green means a visible game
with its verified canonical, role-gated text channel under the shared Games
category, not just a selectable flag or the administrator's personal roles.

Opening or **Refresh** reads one shared channel/role configuration inventory,
using the existing visibility planner, identity checks and overwrite policy.
No chat history is fetched and no Discord or database writes occur. Extra access,
stale mappings, uncertain reservations and unfinished operations cannot appear
ready. Failed or denied reads show **Unverified**, not a missing channel.

- **Ready** (green): the visible game and its chat are verified; click stages Hide.
- **Not set up**: hidden game with no associated chat; click stages Show / setup.
- **Set up**: the visible game still needs its chat, placement, role or access
  policy completed; click stages setup directly, not an unwanted Hide first.
- **History kept**: hidden retained channel, distinct from having no channel.
- **Review / Unfinished / Refresh**: the saved or observed state needs attention.
  A review-only game explains its blocker when clicked.
- **Show pending / Hide pending** (blue): an unsaved choice, never green success.
  Clicking again clears the choice without changing the saved state.

Readiness is a snapshot, not a live subscription. After 90 seconds, subsequent
renders/clicks require Refresh for unverified choices; no background message
editing or automatic repair is started. Refresh preserves pending choices and
their original catalog versions. A catalog edit cannot be silently accepted
under an older choice. **Back to games** after applying a batch clears its old
drafts and rechecks actual outcomes, including partial failures; it does not
retry them automatically. See [readiness acceptance](GAME_READINESS_ACCEPTANCE.md).

Clicks stage changes only. Choices survive page/range changes. **Show page** or
**Hide page** stages that page only; Show page also explicitly prepares already
visible games whose chats are still missing. Unvisited games are never hidden by
omission. **Clear choices** or **Cancel** performs no writes. Up to 50 explicit
choices fit one review; larger libraries remain browsable in further batches.

**Review changes** fetches one configuration inventory, rechecks the selected
catalog records and existing visibility plans, and displays per-game show/setup,
hide, and blocked outcomes with pagination. Projected category/server/role
capacity and channel soft limits are checked across the batch. Hidden channels
still occupy space. Soft-limit overrides require the explicitly labelled
confirmation; blocked games remain unchanged and their reasons are retained.

A single **Confirm changes** applies all ready items in the reviewed batch,
including items on other review pages, through `game_visibility_service.apply`.
Each item rechecks current authorization, game/role/channel signatures and limits.
Only the batch's own completed shared-category creation/link/rename can advance
later reviewed category plans; external changes still cause a conflict. Existing
creation reservations, per-game operation states, locks and non-destructive
Show/Hide behavior remain authoritative. Single-game commands are unchanged.

Batches are intentionally not atomic: results list each success or failure and
leave already completed games in place. No blind retries or rollback deletions.
Repeated/concurrent confirmation is rejected. Unexpected errors stop remaining
items rather than marking them successful. Drafts are not persistent across bot
restarts: reopen the selector and review current state; already saved individual
changes and pending/uncertain-operation protections remain persistent. Catalog
changes by another administrator invalidate affected old choices. Additional
access grants and stale mappings are still review gates, not bypassed by bulk UI.

Offline coverage: `tests/test_admin_game_visibility.py` covers private access,
readiness-based display, 120-game browsing, staged page controls, cancellation, stale reviews,
three-game creation with a missing category, mixed Show/Hide, old detached chats,
capacity/soft limits, partial failures, uncertain creates, and duplicate confirms.
Live acceptance: choose a few games on different pages, review and confirm once;
verify one shared category, expected role-gated channels, preserved IDs/history on
Hide/Show, unchanged unselected games, and no duplicate channels on repeat.

Focused readiness regressions: `python -m pytest tests/test_game_readiness.py tests/test_admin_game_visibility.py`. The full locked CI and owner-controlled live test remain separate release gates.


## Game channel recovery

A game shown as **🛠️ Repair** in **Manage Visible Games** opens a scoped,
owner/admin recovery review instead of ending at a technical mapping error.
Recovery uses only persisted channel IDs and legacy chat evidence; it never
adopts a channel merely because its name looks similar.

- If a saved game-chat ID is confirmed absent from a fresh Discord channel
  inventory, the owner can review **Create new channel**. Confirmation clears
  only the dead game-chat mapping/tombstone and a matching completed creation
  reservation, then delegates to the normal Show lifecycle to create the new
  role-gated chat under **🎮 Games**.
- If one or more saved existing text chats are still present, the review lists
  them explicitly. **Use #channel** requires a second confirmation, then links
  that exact ID and lets normal Show move/rename it into Games while preserving
  its ID and message history.
- **Create new channel** remains an explicit alternative when a valid old chat
  exists. The old channel is not deleted; only this game's association is
  replaced after confirmation.
- A saved ID that now points to a non-text resource, a chat referenced by
  another game, unexpected positive access, unsafe role hierarchy, malformed
  metadata, or an uncertain/PENDING creation remains blocked for manual review.
  Unknown API results are never treated as proof of deletion.

Recovery previews are read-only, actor-bound and short-lived. Confirmation
rechecks the game and stored evidence before any mapping write. No LFG/voice
channel, unrelated legacy resource, member role or message history is deleted.
