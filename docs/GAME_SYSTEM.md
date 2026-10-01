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
