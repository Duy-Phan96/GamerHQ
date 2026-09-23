# Onboarding and optional role settings

Welcome's persistent **Get Started** button opens an ephemeral flow:
**Gender (optional) → Age group (optional) → the existing game selector → complete**.
Skipping either profile question changes nothing and never gates server access.
Gender uses Male, Female and Prefer not to say; age uses Under 18, 18–24, 25–34 and
35+. No adult-only policy was found in repository rules, and this feature introduces
no age restriction or verification. No free text or birthday is stored. The selected
roles are visible on members' Discord profiles; the prompt explains this.

## Optional settings

`choose-your-roles` stays public/read-only and contains these separate pinned boards:

1. Optional Settings intro, with Update Profile and Suggest Role.
2. Notifications: Community Events, Stream Updates, Giveaways.
3. Gaming Content: Gaming News, Gaming Deals.
4. Language: English, German.
5. Platform: PC, PlayStation, Xbox, Nintendo, Mobile.

Each button toggles only the clicking member's registered role and confirms privately.
The handler rereads the member and mapping, rejects missing/privileged/unassignable
roles, and serializes clicks per member. Gender/age choices are exclusive within each
group; platforms and languages allow multiple choices. Notification roles are never
automatically assigned. Stream Updates is retained for compatibility with the existing
streamer area; automatic live promotion remains that integration's separate work.

Persistent entry points are registered by `Roles.cog_load` and the existing Games cog.
The personal onboarding/game selection sessions expire after five minutes; reopen the
persistent entry button after expiry or a bot restart.

## Persistence and migration

Base roles reuse `managed_roles` with kind `base` and stable keys. Existing language
and platform IDs are reused. Setup creates missing roles; unsafe/ambiguous matches
require review instead of creating duplicates. Discord roles have no added privileges.

The intro reuses `server_pinned_message_<channel-id>` and replaces the old giant default
message in place. Category keys are `role_message:<guild-id>:notifications`,
`:gaming_content`, `:language`, and `:platform`. They use the existing fixed-message
helper, content/editor registry, fingerprints, locks, persisted IDs and pin recovery.
Each board can be edited independently through `/server pinned-messages`; customized
content survives refresh. Missing boards are recovered without reposting healthy boards.
Initial creation follows the listed order. Discord cannot move an existing message;
recovery of a deleted middle board appends its replacement instead of deleting others.

Competitive, Casual and global LFG Pings are removed from the active configuration/UI.
Sync retires only those recorded base-role mappings. It does not delete their Discord
roles or remove existing member roles. No repository LFG dependency on those roles was
found. Unknown roles with similar names remain untouched; any later Discord deletion
needs owner review of live permissions, integrations and membership.

## Per-game LFG opt-in

The existing Choose Games intro gains **LFG Notifications**, opening the same categorized
game selector in notification mode. Categories with more than 25 choices are paginated.
Game selection and notification membership are independent; neither enables the other.
No per-game permanent notification messages are created.

Owner setup/role sync derives `🔔 <game name> LFG` roles from selectable database games.
Mappings use `managed_roles` kind `lfg`, key `<game-id>`. Hidden/deleted games are no longer
selectable; existing memberships are retained. Orphan mappings require review. Run role
sync after changing the game catalog. An unrecorded matching LFG role is never blindly
adopted/recreated; this avoids duplicates after uncertain creation failures.

Only the first successful public LFG post mentions that game's opt-in role. Private events,
secondary posts and refreshes never ping it. Missing/unsafe mappings suppress the ping,
not event creation. Discord must permit GamerHQ to mention the role for delivery; game
notification membership grants no game-area access.

## Health and owner acceptance

`/server health` remains read-only and checks role mappings/safety, duplicates, game LFG
references and role-message IDs. Message inspection checks ownership/pins and scans the
latest 100 messages for duplicate default category titles; customized titles outside
that window cannot be exhaustively detected.

After deploying one bot instance:

1. `/server setup` → Repair / Setup → Confirm Repair.
2. `/server roles` → Sync Roles → Confirm Sync (also available after catalog changes).
3. `/server health` and `/server pinned-messages` to inspect the five boards.
4. Test Get Started, skipping both profile questions, ordinary game selection and separate
   LFG opt-in. Verify a second role-button click removes its role.

Customized Welcome content/buttons remain owner-controlled. If it predates onboarding,
add the allowlisted Get Started action in the pinned-message editor.

OWNER ACTION REQUIRED: configure the external Instant Gaming bot manually:

- Campaigns → mention **🔥 Gaming Deals**.
- Gaming News → mention **📰 Gaming News**.

Use GamerHQ's newly registered roles and permit the integration to mention them. GamerHQ
does not configure the external bot. Do not substitute game-access roles or the retired
global LFG role. Live Discord/hierarchy/notification delivery still needs owner acceptance.
