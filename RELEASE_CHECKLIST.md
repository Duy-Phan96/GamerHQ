# GamerHQ Release Checklist

Run this after every release candidate before declaring it stable.

## Automated gates

- [ ] `python -m pytest` passes (or `python -m tools.test` for unittest with offline isolation).
- [ ] `python -m tools.production_preflight --db-path <runtime-db>` passes.
- [ ] `git status` contains no `.env`, DB, logs, backups or runtime data.
- [ ] Container image builds successfully on the deployment host.
- [ ] A verified pre-deployment SQLite backup exists outside the image.
- [ ] Follow `DEPLOY.md`: external `.env`/data/backups, SELinux labels, backup timer and restore drill verified.
- [ ] Compose reports healthy, and the PC instance remains stopped.

## Startup & persistence

- [ ] Instant Gaming: follow `docs/INSTANT_GAMING.md`; sync twice, verify four stable channel/pin IDs and restart persistence. News/Deals are public read-only; Purchases/Buyer Ranking are staff-only. Test Affiliate Stats category policy and bot access, links/embeds/attachments before enabling external feeds.

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
- [ ] As host, edit/manage participants and decide time proposals; deny unrelated users; restart and verify missing-card recovery without duplicates.

## Voice

- [ ] Verify the configured Music Bots role can use intended public voice/text areas but cannot access STAFF, tickets or private LFG channels; cleanup must require preview and confirmation.
- [ ] Setup sync preserves STAFF/private access and does not delete cleanup candidates.

- [ ] Game `create-voice` generator creates a temporary room.
- [ ] Temporary room is deleted after it becomes empty.

## Guides

- [ ] `bot-commands` contains one concise GamerHQ/music guide and remains writable.
- [ ] Verify welcome/newbies history, read-only START HERE, protected manual pins, and repeat repair without duplicates.
- [ ] `streamer-commands` exists only in the Streamers category.
- [ ] `mod-commands` remains staff-only.
- [ ] Restart updates guides without duplicates.

## Community migration

- [ ] `tournaments` exists with Coming Soon copy.
- [ ] `giveaways` exists with Coming Soon copy.
- [ ] Old community memes/clips channels are not duplicated.


## Core structure and suggestions

- [ ] Review [Discord setup](docs/DISCORD_SETUP.md) and deploy the reviewed schema/cog update.
- [ ] Owner runs setup twice; compare IDs, categories, pin counts and custom overrides.
- [ ] Check read-only boards, private staff visibility, modal delivery and status buttons after restart.
- [ ] Verify LFG, selectors, voice, music, tournament/giveaway and streamer regressions live.


## Voice and Area controls

- [ ] Use [command reference](docs/COMMANDS.md); verify area and voice command registration.
- [ ] Run owner setup twice; confirm guide name/ID/pin preservation.
- [ ] Test owner/staff Voice permissions, lock/music, restart ownership and empty cleanup.
- [ ] Test multi-page Area creation and confirmed removal; confirm active-resource blocks and game/role preservation.


## Owner acceptance preparation

- [ ] Review this checklist and [command reference](docs/COMMANDS.md).
- [ ] Deploy/sync; run owner/admin health, inspect private Details, then owner-confirmed setup repair.
- [ ] Repeat health/setup; verify stable IDs/pins and manual-review protection.
- [ ] Execute this checklist with two member accounts and a Staff account, including revoked permissions.
- [ ] Record unresolved manual findings; do not run unknown-resource deletion as a repair shortcut.


## Affiliate support board

- [ ] Review [partner defaults and migration](docs/PARTNERS.md) and the exact configured URLs.
- [ ] Owner runs setup twice; verify the overview and five partner channels each have one canonical pin, preserved IDs/history and no duplicate active flows.
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

Ticket behavior and current limits are documented in [README](README.md) and [architecture](docs/ARCHITECTURE.md).

## Haushaltscheck, managed editor and external deal posting

- [ ] START HERE retains need-support and support-gamerhq; overview has five unique mapped mentions and the short affiliate disclosure.
- [ ] PARTNERS & BENEFITS is ordered Gaming News, Gaming Deals, Amazon, AI Tools, Haushaltscheck. Repair retires managed direct-support safely; the existing support-gamerhq pin explains both ways to support. Verify texts/links against docs/PARTNERS.md.
- [ ] Test migration from separate strom-gas/finanzberatung and older germany-services. Recorded channels are reused when appropriate; default old flows retire only after new pins exist. Unknown/manual/customized content survives and retained channels are reported for MANUAL_REVIEW.
- [ ] Confirm new course/energy/finance requests cannot be created; existing tickets keep readable history and close/restart behavior.
- [ ] Haushaltscheck creates HOUSEHOLD_CHECK_REQUEST, private to creator/Staff/bot. User B cannot view user A's request; repeated clicks do not duplicate it.
- [ ] Owner/admin can edit Markdown/buttons, preview, save in place and explicitly reset each supported board. Member/moderator without admin is denied. Repeated repair/restart preserves custom text/buttons and IDs; unrelated pins remain untouched.
- [ ] Customized legacy overview/deal copy is reviewed explicitly before Reset to Default; normal repair does not silently replace it.
- [ ] Instant Gaming setup follows docs/INSTANT_GAMING.md. Verified bot ID can post in all four feeds. Purchase notifications/buyer ranking remain disabled externally until private-channel acceptance; then route only to their respective Affiliate Stats channels.
- [ ] Host build, Compose health, SELinux mounts, daily timer, off-host backup and restore drill pass before stable VPS release.

- [ ] Confirm English defaults/buttons; Haushaltscheck remains German. Amazon offers a manual Ctrl + D tip. Gaming Deals uses the requested automatic-feed text and retains its affiliate button; overview disclosure remains.
- [ ] Completed migrations remove obsolete finance/energy mappings from active settings while preserving historical IDs/custom content for review. Finance is not a required partner channel; repeated repair creates no replacement finance flow.

- [ ] Verify the updated support explanation, five unique mentions, neutral affiliate labels and retirement of the separate direct-support channel.
- [ ] Verify empty/known managed finanzberatung deletes only through owner Repair; health and sync leave it intact. Check exact MANUAL_REVIEW reasons for manual/custom content, missing identity, dependencies, threads, denied inspection and renamed/protected channels.
- [ ] Verify stale finance editor/channel/message mappings retire while historical tickets/audit remain.

- [ ] Bot organization: verify gift-emoji Free Games immediately below Deals, DealGecko scoped access, private Affiliate Stats inherited/explicit IG access, both hoisted groups below Staff and no duplicate resources after repeated Repair.
