# GamerHQ Release Checklist

Run this after every release candidate before declaring it stable.

## Automated gates

- [ ] `python -m unittest discover -s tests -v` passes.
- [ ] `python -m tools.production_preflight --db-path <runtime-db>` passes.
- [ ] `git status` contains no `.env`, DB, logs, backups or runtime data.
- [ ] Container image builds successfully on the deployment host.
- [ ] A verified pre-deployment SQLite backup exists outside the image.

## Startup & persistence

- [ ] Bot starts without traceback/errors.
- [ ] `/game-admin database` points to the expected persistent database.
- [ ] Custom games such as Elden Ring/Baldurs Gate 3 are still present after restart.
- [ ] No duplicate managed channels/messages are created after a second restart.

## Games

- [ ] Choose Your Games opens and game buttons respond.
- [ ] Add Game confirmation assigns the role.
- [ ] Remove Game confirmation removes the role.
- [ ] `/game select` works.
- [ ] One multiplayer game area has `chat`, `looking-for-group`, `create-voice` only.
- [ ] One solo game area has `chat`, `create-voice` only.

## LFG / Events

- [ ] The pinned `Create Event` button responds immediately.
- [ ] `/lfg create` opens the same event builder.
- [ ] Public event can be created.
- [ ] Public event Join / Leave work.
- [ ] Public `Share Event` returns a usable Discord link.
- [ ] Private event can be created with its private text channel.
- [ ] Invited user can join.
- [ ] User missing the game role can receive it during join.
- [ ] Private participant permissions are correct.
- [ ] Event voice is created according to the configured reminder.
- [ ] Event cancellation shows final status, waits for occupied voice, and cleans up cards/private resources after 24 hours.
- [ ] Complete the Active Lobby manual checklist in `ACTIVE_LOBBY_REPORT.md`, including permissions, proposals, restart persistence and missing-card recovery.

## Voice

- [ ] Complete the Music Bots permission and cleanup acceptance checklist in `MUSIC_CLEANUP_REPORT.md`.
- [ ] Setup sync preserves STAFF/private access and does not delete cleanup candidates.

- [ ] Game `create-voice` generator creates a temporary room.
- [ ] Temporary room is deleted after it becomes empty.

## Guides

- [ ] `bot-commands` contains one concise GamerHQ/music guide and remains writable.
- [ ] Complete `ONBOARDING_REPORT.md` checks: welcome/newbies migration, read-only START HERE, protected user pins, and repeat update without duplicates.
- [ ] `streamer-commands` exists only in the Streamers category.
- [ ] `mod-commands` remains staff-only.
- [ ] Restart updates guides without duplicates.

## Community migration

- [ ] `tournaments` exists with Coming Soon copy.
- [ ] `giveaways` exists with Coming Soon copy.
- [ ] Old community memes/clips channels are not duplicated.


## Core structure and suggestions

- [ ] Review COMMUNITY_STRUCTURE_REPORT.md and deploy the additive schema/cog update.
- [ ] Owner runs setup twice; compare IDs, categories, pin counts and custom overrides.
- [ ] Check read-only boards, private staff visibility, modal delivery and status buttons after restart.
- [ ] Verify LFG, selectors, voice, music, tournament/giveaway and streamer regressions live.


## Voice and Area controls

- [ ] Review VOICE_AREA_REPORT.md; deploy/sync new area and voice commands.
- [ ] Run owner setup twice; confirm guide name/ID/pin preservation.
- [ ] Test owner/staff Voice permissions, lock/music, restart ownership and empty cleanup.
- [ ] Test multi-page Area creation and confirmed removal; confirm active-resource blocks and game/role preservation.


## Owner acceptance preparation

- [ ] Review ACCEPTANCE_PREPARATION_REPORT.md and COMMAND_INVENTORY.md.
- [ ] Deploy/sync; run owner/admin health, inspect private Details, then owner-confirmed setup repair.
- [ ] Repeat health/setup; verify stable IDs/pins and manual-review protection.
- [ ] Execute the report’s LFG/Voice/suggestion/restart/permission checklist with test accounts.
- [ ] Record unresolved manual findings; do not run unknown-resource deletion as a repair shortcut.


## Affiliate support board

- [ ] Review SUPPORT_REPORT.md and the exact configured URLs.
- [ ] Owner runs setup twice; verify one category/channel/pin and preserved history/IDs.
- [ ] Test member read-only permissions and Staff/bot posting; inspect guide reference.
- [ ] Confirm optional-use disclosure, Amazon bookmark wording, and absence of unrelated promotions/DMs.

## Private support ticket acceptance

- [ ] Back up SQLite; deploy/restart the reviewed code manually. Run `/server health`.
- [ ] Preview and confirm owner `/server setup`; verify both support entries are in START HERE and existing affiliate ID/history/pin are preserved. Run setup twice.
- [ ] Users A/B create distinct tickets; each sees only their own. Moderator sees both; neither user sees ticket-logs or can manage channel permissions.
- [ ] Double-submit, open-limit, required form fields and stale/unauthorized buttons behave safely.
- [ ] Staff takes a ticket, marks waiting, and resumes; a second Staff cannot replace an active assignee accidentally.
- [ ] Creator cancels close, then confirms; history remains readable and posting is locked. Staff can also close. Repeated close is harmless.
- [ ] Restart: assignment/state, entry and ticket buttons survive; closed tickets stay closed and no duplicate pins/channels appear.
- [ ] Creator departure and Staff role revocation preserve history, clear invalid assignment and revoke historical ticket/log access.
- [ ] Verify health diagnostics, private audit delivery and category-capacity warnings. Review any uncertain reservation before manual correction; never recreate blindly.
- [ ] Confirm exact affiliate links, optional wording and concise separate guide sections. No donation feature is present.

Full details and current limitations: [TICKETS_REPORT.md](TICKETS_REPORT.md).
