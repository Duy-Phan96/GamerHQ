# Commands

Generated from current discord.py command group objects, without bot login or command synchronization. Runtime `/server health` Details shows the deployed inventory.

## Member commands

- `/game select` — Add or remove one of your game roles.
- `/game suggest` — Suggest a game that GamerHQ should add.
- `/lfg create` — Create a GamerHQ Looking for Group event.
- `/lfg join-code` — Join a private GamerHQ event using its invite code.
- `/lfg manage` — Manage or cancel LFG events you created.
- `/streamer area` — Create or open your optional GamerHQ Streamer area.
- `/streamer audience` — See who follows your GamerHQ Streamer profile.
- `/streamer channels` — Manage your 3 permanent Streamer community channels.
- `/streamer profile` — View your GamerHQ Streamer profile.
- `/streamer setup` — Create or update your GamerHQ Streamer profile.
- `/streamer voice` — Create a temporary Voice room in your Streamer area.
- `/voice manage` — Manage your own Create Voice room; staff may select a room.

## Moderator / Staff actions

Staff review suggestions and take/mark waiting/close support tickets via private persistent buttons. `/voice manage` permits Staff to select a managed room. Other actions remain scoped to the creator/host or current command authorization. These are not additional slash commands.

## Admin / Owner commands

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
- `/server cleanup-game-areas` — Owner/admin: preview unused managed game areas before confirming cleanup.
- `/server health` — Owner/admin: read-only diagnostics and acceptance-test details.
- `/server music-bots-role` — Admin: configure the existing dedicated Music Bots role.
- `/server pinned-messages` — Admin: create or edit managed pinned channel messages.
- `/server roles` — Admin: sync, review and safely clean up GamerHQ-managed roles.
- `/server sync-support` — Admin: synchronize and pin the Support and partner messages in configured channels; overview bullets use managed channel mentions.
- `/server setup` — Owner only: inspect and repair core channels, guides and private suggestions.

`/server setup` is owner-only. Health and cleanup check owner/admin access; other administrative commands retain their Administrator checks. Destructive Game Area/library actions require their existing previews/confirmations. General member commands never grant server administration.

Persistent components include game/role selectors, LFG cards/invites/proposals, suggestions and support ticket entry/actions. Temporary Voice panels can be reopened after restart. Events remain Coming Soon; no XP commands exist.

If a partner mapping/channel is missing, `/server sync-support` omits that destination from the overview and reports incomplete setup. `/server health` identifies missing mappings; owner `/server setup` repairs them. Sync does not create replacement channels.
