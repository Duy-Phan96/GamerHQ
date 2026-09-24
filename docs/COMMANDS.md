# Commands

Generated from current discord.py command group objects, without bot login or command synchronization. Runtime `/server health` Details shows the deployed inventory.

## Member commands

- `/game select` — Add or remove one of your game roles.
- `/game suggest` — Suggest a game that GamerHQ should add.
- `/lfg create` — Create a GamerHQ Looking for Group event.
- `/lfg join-code` — Join a private GamerHQ event using its invite code.
- `/lfg manage` — Manage or cancel LFG events you created.
- `/voice manage` — Manage your own Create Voice room; staff may select a room.

## Moderator / Staff actions

Staff review suggestions and take/mark waiting/close support tickets via private persistent buttons. `/voice manage` permits Staff to select a managed room. Other actions remain scoped to the creator/host or current command authorization. These are not additional slash commands.

## Admin / Owner commands

- `/deals import-gocdkeys` — Owner/admin: paste 1–10 partner links into the modal, review paginated results, optionally Edit Titles/notes, then Post New Deals or Cancel. URL duplicates and existing delivery claims are skipped. See [manual import](GOCDKEYS.md#manual-batch-import).

- `/deals create` — Owner/admin: select Amazon, Instant Gaming, GoCDKeys or Other; enter prices and a verified product/affiliate URL; Preview → Post Deal / Edit / Cancel. Optional image URL; fixed gaming-deals target. See [curated deals](DEALS.md).

- `/deals backfill` — Owner/admin: currently unavailable; automatic GoCDKeys access is unsupported (HTTP 403). No history processing or posts. Use verified manual links via `/deals create`.

- `/area manage` — Add or safely remove multiple Game Areas.
- `/game-admin add-area` — Admin: create or restore a dedicated Discord area for a library game.
- `/game-admin create` — Admin: create a new Game Library entry and its game role.
- `/game-admin database` — Admin: show the runtime GamerHQ database path and Game Library counts.
- `/game-admin delete` — Admin: permanently delete a library game and role after its area is safely removed.
- `/game-admin overview` — Admin: create or refresh the GamerHQ Choose Your Games overview.
- `/game-admin recover-existing` — Admin: reconnect an orphaned Discord game role/area to the Game Library.
- `/game-admin remove-area` — Admin: remove only a game's dedicated Discord area.
- `/game-admin rename` — Admin: safely rename an existing GamerHQ game.
- `/game-admin set-visible` — Admin: show/hide a library game in Choose Your Games without changing its area.
- `/game-admin setup` — Admin: inspect/reconcile Discord areas enabled in the Game Library.
- `/game-admin status` — Admin: inspect one game's DB state and linked Discord resources.
- `/server adopt channel:<managed channel> aspect:<name|category|position|all>` — Owner/admin: compare current Discord layout with desired state, then explicitly confirm selected properties. Initial scope: Support GamerHQ and the five public partner/feed channels. Permissions are not imported. See [desired state and adoption](SERVER_STRUCTURE.md#explicit-channel-adoption).
- `/server cleanup-game-areas` — Owner/admin: preview unused managed game areas before confirming cleanup.
- `/server health` — Owner/admin: read-only diagnostics and acceptance-test details.
- `/server instant-gaming` — Admin: sync public gaming-news/gaming-deals and private Affiliate Stats purchases/buyer-ranking. Reports channels, permissions, pins and bot access using INSTANT_GAMING_BOT_ID.
- `/server music-bots-role` — Admin: configure the existing dedicated Music Bots role.
- `/server pinned-messages` — Owner/admin: edit GamerHQ-managed pinned messages and buttons. Select channel → select message (automatic for one pin) → edit content/buttons → Preview → Save Changes. Multiple pins, persistent customization and confirmed Reset to Default are supported. See [managed message editing](MANAGED_MESSAGES.md).
- `/server roles` — Admin: sync, review and safely clean up GamerHQ-managed roles.
- `/server sync-support` — Admin: reconcile existing Support/partner names, parents and order with desired state, and synchronize their pinned messages; overview bullets use managed channel mentions.
- `/server setup` — Owner only: inspect and repair core channels, guides and private suggestions.

`/server setup` is owner-only. Health, cleanup and the pinned-message editor check owner/admin access; other administrative commands retain their Administrator checks. Moderator permissions alone do not grant message-editor access. Destructive Game Area/library actions require their existing previews/confirmations. General member commands never grant server administration.

Persistent components include game/role selectors, LFG cards/invites/proposals, suggestions and support ticket entry/actions. Temporary Voice panels can be reopened after restart. Events remain Coming Soon; no XP commands exist.

If a partner mapping/channel is missing, `/server sync-support` omits that destination from the overview and reports incomplete setup. `/server health` identifies missing mappings; owner `/server setup` repairs them. Sync does not create replacement channels.

Electricity uses the persistent `ELECTRICITY_REQUEST` button and existing private ticket actions, not a new slash command. Old energy/course/finance entry actions are retired. The optional external Instant Gaming bot has its own `/config`; it is not a GamerHQ command.

Owner `/server setup` Repair also ensures Free Games immediately below Gaming Deals, a private AFFILIATE STATS category, and hoisted Music Bots/Gaming Bots roles for explicitly configured bot user IDs. `/server health` reports missing optional bot identities and unsafe hierarchy/access without changing state.

`/server health` checks DealGecko/Gaming Bots paid-channel access and comparison configuration. `/server setup` Repair restores scoped feed permissions; it does not configure the external DealGecko dashboard.

## Streamer Hub (hidden beta)

Connect/disconnect Twitch through the private buttons in streamer-guide; no new
slash commands are needed. Both feature flags default false. Legacy `/streamer setup` and `/streamer profile` open the enabled beta; `/streamer audience` is
inactive. Existing `/streamer area`, `/streamer channels` and `/streamer voice`
remain staff-only legacy tools with server-side authorization, including when Twitch is disabled.
Public profiles and choose-streamers are inactive. See [Streamer Hub](STREAMER_HUB.md).

## Profile controls

Welcome **Get Started** and choose-your-roles **Update Profile** use Gender → Age →
Review → Save Profile. Only Save changes personal roles; current values are preselected
and Back/Cancel are safe. Gaming Setup and Interests & Notifications have independent
quick toggles. Empty playstyle sections are hidden. Suggest Role is the last board.
Games remain exclusively in choose-your-games. GamerHQ is English, with no language
selection. Main controls survive restart; unfinished drafts do not. See
[profile settings](ROLE_SETTINGS.md) for explicit Repair and legacy-role retention.
