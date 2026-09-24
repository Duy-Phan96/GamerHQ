# Profile Settings and game selection

Choose Your Games manages game membership (and the existing separate per-game LFG
notification selector). Choose Your Roles is **Profile Settings**: personal profile,
gaming setup, interests and notifications. Update Profile never opens a game selector.
Welcome's Get Started button opens the same profile editor; games remain a separate step
in choose-your-games.

## Profile Settings

The public/read-only choose-your-roles channel has five canonical pinned messages:

1. **Profile Settings** — Update Profile, with one concise explanation.
2. **About You** — Gender, Age group, Language quick controls.
3. **Gaming Setup** — Platform quick controls.
4. **Interests & Notifications** — Community Events, Stream Updates, Giveaways,
   Gaming News and Gaming Deals quick controls.
5. **Missing something?** — Suggest Role, separate from Update Profile.

Gender supports Male, Female, Non-binary / Diverse and Prefer not to say. The last
choice clears managed gender roles; it creates no visible role. Existing legacy
Prefer not to say roles are left on the server and cleared from a member only on
explicit profile save/clear. Gender and age are optional, with at most one each.
Age groups remain Under 18, 18–24, 25–34 and 35+; no birthday is collected or stored.
English/German and PC/PlayStation/Xbox/Nintendo/Mobile support multiple selections.
Roles are visible on members' server profiles; age is not verified or an access gate.

There are **no active playstyle roles** in the current repository. Casual, Competitive
and global LFG Pings stay retired. The Playstyle wizard step explains this and offers
Next; it does not recreate these roles or remove legacy member assignments.

## Update Profile

**Gender → Age → Language → Platform → Playstyle → Interests & Notifications →
Review → Save Profile**. The private session fetches the member's current roles and
preselects mapped profile choices. Back preserves the draft; Cancel/timeout changes
nothing. Empty optional fields are omitted from review. Save is unavailable until review.

Only Save calculates and applies profile differences. The shared per-member preference
lock serializes quick actions and saves. Member roles and mappings are rechecked; a
changed profile/mapping rejects the stale draft and asks the user to reopen. Game, LFG,
Staff/Admin, bot/integration and unrelated roles are preserved. Unsafe or overlapping
mappings fail closed. Gender/age exclusivity is validated again server-side.
Discord role additions/removals are separate requests: a save-time API error can leave
some confirmed changes applied, and the response tells the member to reopen and inspect.
No role changes occur while moving through the draft.

Quick controls toggle only that setting immediately; gender/age replace only their
exclusive group. They do not open the wizard. All controls authorize the clicking member;
private wizard buttons also bind to the member and guild. Suggest Role uses the existing
suggestion submission/review flow. Notifications are explicit opt-ins.

Persistent main controls retain custom IDs and are registered by Roles.cog_load.
Draft sessions expire after five minutes and do not survive restart; restart Update
Profile from the persistent button. No separate profile database or personal data fields
are introduced: managed_roles plus Discord membership remain authoritative.

## Persistence and migration

Setup reuses existing role IDs and the same five message slots, updating defaults in
place: intro → Profile Settings, notifications → About You, gaming_content → Gaming
Setup, language → Interests & Notifications, platform → Missing something?. Those
legacy key names remain internal stable identifiers. The new Diverse role is ensured
through the existing base-role helper; Prefer not to say is no longer created.

The fixed-message helper retains IDs, pins, fingerprints, locks, recovery and custom
content. Repeated setup/repair does not duplicate roles/messages. Legacy customized
controls remain valid and are retained; health flags their layout for review. Use
/server pinned-messages → Reset to Default → Confirm Reset on each affected custom
pin to adopt the new grouping. No customized message is silently overwritten.

Initial creation and the standard existing slots follow the listed order. Discord
cannot move a message; recovering a deleted middle slot appends its replacement
rather than deleting/reposting healthy messages. Manually reordered/previously
recreated slots need owner review. Unknown/user messages are never adopted or deleted.

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
references, profile/game channel separation and role-message IDs. It also checks persistent
Update Profile/Suggest Role registration and age/gender mappings. Message inspection checks ownership/pins and scans the
latest 100 messages for duplicate default category titles; customized titles outside
that window cannot be exhaustively detected.

After deploying one bot instance:

1. `/server setup` → Repair / Setup → Confirm Repair.
2. `/server roles` → Sync Roles → Confirm Sync (also available after catalog changes).
3. `/server health` and `/server pinned-messages` to inspect the five boards.
4. Test Update Profile, Back, Cancel, Review and Save. Verify no roles change before Save,
   games are absent, Suggest Role is at the bottom, and quick controls remain immediate.
5. Check game selection and separate LFG opt-in still work in choose-your-games.

Customized Welcome content/buttons remain owner-controlled. If it predates onboarding,
add the allowlisted Get Started action in the pinned-message editor.

OWNER ACTION REQUIRED: configure the external Instant Gaming bot manually:

- Campaigns → mention **🔥 Gaming Deals**.
- Gaming News → mention **📰 Gaming News**.

Use GamerHQ's newly registered roles and permit the integration to mention them. GamerHQ
does not configure the external bot. Do not substitute game-access roles or the retired
global LFG role. Live Discord/hierarchy/notification delivery still needs owner acceptance.
