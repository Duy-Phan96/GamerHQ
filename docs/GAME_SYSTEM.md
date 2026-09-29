# Games: personal selection and optional channels

SQLite is authoritative. The public seed supplies starter metadata; it does not replace owner selection, Discord IDs or deletion tombstones.

## Independent state

- `active` + `selectable` + a nonzero `role_id` make a game available in the personal selector. Visibility means library visibility, not channel existence.
- `channel_id` is nullable: one optional text channel under the shared **🎮 GAMING** category. The role works without a channel.
- Notification roles remain separate opt-ins. Selecting a game never enables pings.
- Legacy area/category/chat/LFG/create-voice columns are migration evidence only. New multi-channel area creation is disabled.

Choose Your Games retains Select Games, Suggest Game, `/game select` and `/game suggest`. The public board carries no personal state. Select Games opens an ephemeral actor-bound panel with green ✅ selected / neutral ➕ add game buttons; clicking one immediately updates that game role and preserves the current range/page. No Save is required. The quick slash command retains its existing confirmation.

Popular shows the current top 25 by cached role-member counts, descending with alphabetical ties. These games also appear in the complete A–Z list. Popular opens by default with 20 game buttons per page (25 games across two pages). Browse A–Z offers nonempty A–E, F–J, K–O, P–T and U–Z ranges, plus 0–9 / Other for remaining names. Game buttons and navigation stay within Discord's 25-component limit; no game dropdown or Save step is needed. A missing role produces a friendly temporary-unavailability message and a private diagnostic; missing mappings are not presented as an empty library. Opening uses one catalog query and one pass over cached members, no Discord inventory/history/member download. Each change refreshes only that member for authorization and preserves unrelated roles. Reopening recomputes Popular.

## Administration

Server Management → Games contains Library, Channels, Candidates, Create/Remove and Suggested Games guidance. Library Show Game creates/reuses a role and makes the game selectable. Hiding preserves membership and channels. New approved games use `/game-admin create`; suggestions retain their existing staff inbox.

Defaults in `config.py`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `GAME_CHANNEL_MEMBER_THRESHOLD` | 10 | Candidate threshold, never automatic creation |
| `GAME_CHANNEL_SOFT_LIMIT` | 20 | Initial operational limit; explicit Create Anyway allowed |
| `POPULAR_GAMES_COUNT` | 25 | Maximum Popular shortcuts; independent of channel count |

After a successful game-role grant, only that game's count is checked. At the threshold a private STAFF **🎮・games-log** candidate is reserved in SQLite before sending. `game_channel_candidates` stores guild/game, timestamp, message ID and PENDING/CREATED/IGNORED state. Repeated grants and restarts do not post it again. An uncertain delivery stays reserved and is available in Games → Candidates; staff can decide there without reposting. Ignore suppresses immediate re-notification.

Creating manually works below threshold. Every creation/migration/removal opens an actor-bound expiring preview and confirmation. Permission, stored IDs and resource signatures are rechecked; simultaneous submissions serialize. A durable creation reservation blocks retries after uncertain delivery: inspect Discord and the stored state privately rather than clearing it blindly. Missing/stale stored channel IDs require review and are never replaced by guessing names.

Channels use normalized names and alphabetical ordering. Everyone is denied visibility; the game role can read and chat; staff and GamerHQ retain access. Repair uses stored channel IDs and scoped permissions; no new per-game category, LFG or create-voice resource is generated. Other channels retain their relative order.

Removal only targets the persisted managed channel after dependency checks and explicit confirmation. Discord channel history is deleted with the channel as the preview warns; the game, role, selection and stored event records remain. Export/archive wanted Discord history before confirming. No routine test or startup deletes live channels. Games-log records administrative actions; high-level channel changes also use server-log. Normal member toggles do not spam either log.

## Legacy migration

Owner Server Dev → Legacy Game Migration lists recorded legacy resources. Preview Migration prefers the existing stored chat ID, moves/renames that channel into GAMING, applies game-role access and persists `channel_id`. History and IDs survive. Old resource IDs are retained in `game_legacy_hints`; old categories, LFG and create-voice resources remain untouched for separate manual review. Never infer ownership of an arbitrary channel from its name. Empty old categories may be removed only through a separately reviewed owner cleanup.

Legacy `/area manage` removal and dependency checks remain; its creation backend now refuses new multi-channel areas. Existing centralized LFG and temporary voice systems remain functional during migration.

For an empty production selector, use the [games-scope recovery dry-run](DATABASE_MIGRATION.md#games-scope-recovery-on-copies). Recover catalog metadata, flags and roles; old areas become hints, never active canonical channels. Production-only fields, settings, runtime tables and already adopted role/channel IDs remain authoritative.

## Implementation and validation

`game_selector`, `game_catalog_service`, `game_channel_service` and the existing role/managed-server services own this lifecycle. Regression coverage includes `test_game_channels_v2.py`, role/security tests and the existing LFG/voice suites. Health is read-only and checks shared category, games-log, stored channels, access, ordering and legacy review evidence. Offline tests are not live Discord acceptance.
