# GamerHQ settings

GamerHQ is an **English-language server**. Language is not a member-selectable setting.
Choose Your Games manages game membership and the existing separate per-game LFG
notification selector. Choose Your Roles provides personal corrections and quick
settings for gaming platforms and interests.

## Initial onboarding and personal corrections

Welcome's persistent **Get Started** button opens the same concise personal flow as
**Update Profile**: **Gender → Age → Review → Save Profile**. The repository has no
separate automatic join questionnaire; the Welcome entry is the existing onboarding
path. Neither entry asks about languages, games, platforms or notifications.

Gender is optional: Male, Female, Non-binary / Diverse, or Prefer not to say. The last
choice clears managed gender roles and creates no visible privacy role. Age groups
remain Under 18, 18–24, 25–34 and 35+; no birthday is collected or stored. These roles
are visible on the server. This is not age verification or a server access gate.

The private editor preselects current personal roles. Back retains the draft; Cancel,
timeout and restart make no changes. Review omits empty choices. Only Save changes
managed Gender/Age roles. Platform, playstyle, notification, game, Staff/Admin,
integration and unrelated manually assigned roles remain untouched.

Member/guild binding, safe registry IDs, fresh member reads and the existing per-member
lock protect saves. A changed personal profile/mapping rejects an old draft. A parallel
platform or notification toggle does not invalidate personal editing. Discord role
additions/removals are separate requests; an API failure during Save can leave some
confirmed changes applied. The response asks the member to reopen and review.

## Visible Choose Your Roles page

1. **Profile Settings** — Update Profile for correcting personal joining details.
2. **Gaming Setup** — “Choose your platforms”; PC, PlayStation, Xbox, Nintendo, Mobile.
3. **Interests & Notifications** — Community Events, Stream Updates, Giveaways,
   Gaming News, Gaming Deals.
4. **Missing something?** — Suggest Role via the existing suggestion workflow.

About You, Gender/Age quick buttons and Language controls are absent. Platform and
interest buttons toggle just that preference immediately; they never open Update
Profile. A Playstyle subsection appears only if active playstyle options exist.
Currently none do: retired Casual/Competitive roles stay retired, with no placeholder
or configuration warning shown to members.

Persistent main controls survive restart. Personal drafts expire after five minutes;
reopen Update Profile. No parallel profile database or personal fields are introduced.

## Explicit Repair and retained IDs

The four surviving message slots retain their IDs: intro, gaming_content (Gaming Setup),
language (Interests & Notifications; an internal legacy key), platform (Suggest Role).
Normal refresh updates generated defaults and preserves customized content. Explicit
`/server setup` → Repair removes the old mapped notifications/About You message only
when bot ownership and the stored fingerprint match. Ambiguous mappings, changed
content and failed deletion retain the record for owner review. Successful deletion
retires its managed content record and clears the active message reference; repeat
Repair does not recreate it. Unrelated/user messages are preserved.

Repository audit found English/German roles used only by the old profile feature.
They stop being offered/created immediately; explicit Repair changes their registry
kind from `base` to `legacy-profile`. Their Discord IDs, permissions and memberships
are retained, protecting unknown live uses. No broad role deletion or membership
migration runs. Legacy language callbacks do not grant roles; Repair strips their
stored buttons from customized surviving pins while preserving other content/buttons.
Custom text that describes obsolete options remains owner-controlled: use the existing
pinned-message editor to update it or confirm Reset to Default.

Initial creation follows the four-part order. Discord cannot move a message; recovery
of a deleted middle pin appends its replacement instead of reposting healthy messages.
Previously reordered/recreated slots require owner review. Health checks persistent
controls, personal and quick-role mappings, message ownership/pins and profile/game
channel separation without writing; pending legacy cleanup is staff-facing only.

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
3. `/server health` and `/server pinned-messages` to inspect the four boards.
4. Test Update Profile, Back, Cancel, Review and Save. Verify only Gender/Age appear and no roles change before Save.
   About You/language controls must be absent; Suggest Role stays last and quick controls remain immediate.
5. Check game selection and separate LFG opt-in still work in choose-your-games.

Customized Welcome content/buttons remain owner-controlled. If it predates onboarding,
add the allowlisted Get Started action in the pinned-message editor.

OWNER ACTION REQUIRED: configure the external Instant Gaming bot manually:

- Campaigns → mention **🔥 Gaming Deals**.
- Gaming News → mention **📰 Gaming News**.

Use GamerHQ's newly registered roles and permit the integration to mention them. GamerHQ
does not configure the external bot. Do not substitute game-access roles or the retired
global LFG role. Live Discord/hierarchy/notification delivery still needs owner acceptance.
