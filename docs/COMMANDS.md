# Commands

Generated from current discord.py command group objects, without bot login or command synchronization. Runtime `/server health` Details shows the deployed inventory.

## Member commands

- `/game select` — Add or remove one of your game roles.
- `/game suggest` — Suggest a game that GamerHQ should add.
- `/lfg create` — Create a scheduled GamerHQ event; no game selection is required.
- `/lfg join-code` — Join a private GamerHQ event using its invite code.
- `/lfg manage` — Manage or cancel only events you created.
- `/voice manage` — Manage your own Create Voice room; staff may select a room.

## Moderator / Staff actions

Staff review suggestions and take/mark waiting/close support tickets via private persistent buttons. `/voice manage` permits Staff to select a managed room. Other actions remain scoped to the creator/host or current command authorization. These are not additional slash commands.

## Admin / Owner commands

Start with `/server manage` for everyday administration. `/server setup` is the
owner's first-time wizard (with advanced rerun after completion); `/server dev`
contains owner-only technical tools. Legacy technical commands below remain for
compatibility. See [operations](PRODUCTION_OPERATIONS.md).

- `/server manage` — Owner/admin: structure, roles, integrations, managed messages, Games, features and private Server Log. Games offers the library, channels, candidates and suggestion guidance.
- `/server dev` — Owner only: Health, Reconcile, Repair, Duplicate Scan, Production Doctor CLI guidance, Resource Mappings, Raw Diagnostics and Legacy Game Migration.
- `/deals import-gocdkeys` — Owner/admin: choose promotion_type AUTO (default), DEAL or GIVEAWAY; paste 1–10 partner links into the modal, review paginated results, optionally Edit Titles/notes, then Post New Promotions or Cancel. URL duplicates and existing delivery claims are skipped. See [manual import](GOCDKEYS.md#manual-batch-import).

- `/deals create` — Owner/admin: select Amazon, Instant Gaming, GoCDKeys or Other; enter prices and a verified product/affiliate URL; Preview → Post Deal / Edit / Cancel. Optional image URL; promotion_type DEAL (default) targets gaming-deals, GIVEAWAY targets giveaways and offers optional prize/end date/note instead of prices. See [curated deals](DEALS.md).

- `/deals backfill` — Owner/admin: currently unavailable; automatic GoCDKeys access is unsupported (HTTP 403). No history processing or posts. Use verified manual links via `/deals create`.

- `/area manage` — Legacy inspection and confirmed cleanup; new multi-channel creation is disabled.
- `/game-admin add-area` — Deprecated; use Server Management → Games → Create Game Channel. Old confirmations cannot create an area.
- `/game-admin create` — Admin: create a new Game Library entry and its game role.
- `/game-admin database` — Admin: show the runtime GamerHQ database path and Game Library counts.
- `/game-admin delete` — Admin: permanently delete a library game and role after its area is safely removed.
- `/game-admin overview` — Admin: create or refresh the GamerHQ Choose Your Games overview.
- `/game-admin recover-existing` — Admin: reconnect an orphaned Discord game role/area to the Game Library.
- `/game-admin remove-area` — Admin: remove only a game's dedicated Discord area.
- `/game-admin rename` — Admin: safely rename an existing GamerHQ game.
- `/game-admin set-visible` — Admin: show/hide a library game in Choose Your Games without changing its area.
- `/game-admin setup` — Legacy area inspection; new area creation is disabled. Use Games for optional single channels.
- `/game-admin status` — Admin: inspect one game's DB state and linked Discord resources.
- `/server adopt channel:<managed channel> aspect:<name|category|position|all>` — Owner/admin: compare current Discord layout with desired state, then explicitly confirm selected properties. Initial scope: Support GamerHQ and the six public Marketplace channels. Permissions are not imported. See [desired state and adoption](SERVER_STRUCTURE.md#explicit-channel-adoption).
- `/server cleanup-game-areas` — Owner/admin: preview unused managed game areas before confirming cleanup.
- `/server health` — Owner/admin: read-only diagnostics and acceptance-test details.
- `/server instant-gaming` — Admin: sync public gaming-news/gaming-deals and private Affiliate Stats purchases/buyer-ranking. Reports channels, permissions, pins and bot access using INSTANT_GAMING_BOT_ID.
- `/server music-bots-role` — Admin: configure the existing dedicated Music Bots role.
- `/server pinned-messages` — Owner/admin: edit GamerHQ-managed pinned messages and buttons. Select channel → select message (automatic for one pin) → edit content/buttons → Preview → Save Changes. Multiple pins, persistent customization and confirmed Reset to Default are supported. See [managed message editing](MANAGED_MESSAGES.md).
- `/server roles` — Admin: sync, review and safely clean up GamerHQ-managed roles.
- `/server sync-support` — Admin: reconcile existing Support/partner names, parents and order with desired state, and synchronize their pinned messages; overview bullets use managed channel mentions.
- `/server setup` — Owner only: first-time setup wizard; completed servers redirect to management with an advanced rerun option.
- `/server reconcile` — Owner/admin: review existing candidates and confirm persisted ID mappings only.
- `/server repair` — Owner/admin: preview and confirm fixes to linked resources; no creation/deletion.

See [production operations](PRODUCTION_OPERATIONS.md) for the preferred workflow.

`/server setup` is owner-only. Health, cleanup and the pinned-message editor check owner/admin access; other administrative commands retain their Administrator checks. Moderator permissions alone do not grant message-editor access. Destructive Game Area/library actions require their existing previews/confirmations. General member commands never grant server administration.

Persistent components include game/role selectors, LFG cards/invites/proposals, suggestions and support ticket entry/actions. Temporary Voice panels can be reopened after restart. Community Events, Tournaments and Giveaways have separate boards; LFG event commands are active. No XP or development/debug commands are registered.

If a partner mapping/channel is missing, `/server sync-support` omits that destination from the overview and reports incomplete setup. `/server health` identifies missing mappings; `/server reconcile` links them; owner setup creates only genuinely missing resources. Sync does not create replacement channels.

Electricity uses the persistent `ELECTRICITY_REQUEST` button and existing private ticket actions, not a new slash command. Old energy/course/finance entry actions are retired. The optional external Instant Gaming bot has its own `/config`; it is not a GamerHQ command.

For existing resources, use reconciliation followed by repair; setup creates missing blueprint resources. Existing specialized partner/Instant Gaming sync retains its ordering policy. Health reports optional bot identities and unsafe access without changing state.

`/server health` checks DealGecko/Gaming Bots paid-channel access and comparison configuration. `/server reconcile`, then `/server repair` (preview and confirm) restores scoped feed permissions; it does not configure the external DealGecko dashboard.

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

For maintainers, [authorization and recovery boundaries](SECURITY_MODEL.md) classify all command groups and their component actions.

`/server message-duplicates` — owner/admin-only global audit of registered canonical boards; optional `managed_key:<key>` restricts it to one board. Review candidates and the mapping/fingerprint recommendation, then explicitly Keep A / Remove B, Keep B / Remove A, Skip or Cancel. Every removal revalidates identity, authorization and runtime references. Keys and limitations: [database reconciliation](DATABASE_MIGRATION.md).

## Personal games and channel decisions

Choose Your Games → Select Games opens a private panel: Popular Top 25 (current
cached role-member counts, alphabetical ties), all selectable games under A–Z,
and immediate add/remove choices. There is no Save step and no LFG Notifications
button on that board. `/game select` retains its quick single-game confirmation;
`/game suggest` and separate notification preferences remain available.

Server Management → Games → Create Game Channel works below the member threshold.
Review the destination/access before confirmation; at the soft limit choose
**Create Anyway** explicitly. Removal has its own preview and confirmation and
preserves the game, role and selection. Games-log candidates offer Create Channel
or Ignore. Ignored/created candidates are not posted repeatedly.

Owner Server Dev → Legacy Game Migration previews reusing the recorded chat ID.
Old LFG/create-voice channels and categories remain for separate reviewed cleanup.
