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

Surviving profile slots retain their IDs and custom content. Generic `/server repair` updates known default boards and pins, but does not delete the former About You message or migrate obsolete controls. Retained legacy content needs separate owner review; do not assume a restart performs retirement.

Legacy English/German roles are no longer offered. Existing memberships stay intact. The separate role-management workflow retains its ownership checks. Generic setup, repair and reconciliation do not retire legacy profile messages or strip customized controls; use the pinned-message editor after review.

Initial creation follows the four-part order. Discord cannot move a message; recovery
of a deleted middle pin appends its replacement instead of reposting healthy messages.
Previously reordered/recreated slots require owner review. Health checks persistent
controls, personal and quick-role mappings, message ownership/pins and profile/game
channel separation without writing; pending legacy cleanup is staff-facing only.

## Per-game LFG opt-in

Choose Your Games now offers only Select Games and Suggest Game. Its former LFG
Notifications section/button is removed. Existing notification memberships and separate
notification services remain independent; game selection never enables pings.
No per-game permanent notification messages are created. See [personal games](GAME_SYSTEM.md).

Owner setup/role sync derives `🔔 <game name> LFG` roles from selectable database games.
Mappings use `managed_roles` kind `lfg`, key `<game-id>`. Hidden/deleted games are no longer
selectable; existing memberships are retained. Orphan mappings require review. Run role
sync after changing the game catalog. An unrecorded matching LFG role is never blindly
adopted/recreated; this avoids duplicates after uncertain creation failures.

Only the first successful public LFG post mentions that game's opt-in role. Private events,
secondary posts and refreshes never ping it. Missing/unsafe mappings suppress the ping,
not event creation. Discord must permit GamerHQ to mention the role for delivery; game
notification membership grants no dedicated game-channel access.

## Health and owner acceptance

`/server health` remains read-only and checks role mappings/safety, duplicates, game LFG
references, profile/game channel separation and role-message IDs. It also checks persistent
Update Profile/Suggest Role registration and age/gender mappings. Message inspection checks ownership/pins and scans the
latest 100 messages for duplicate default category titles; customized titles outside
that window cannot be exhaustively detected.

After deploying one bot instance:

1. `/server reconcile`, then `/server repair` (preview and confirm).
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
