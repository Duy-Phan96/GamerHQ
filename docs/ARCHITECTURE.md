# Architecture

Python/discord.py interaction layer (`cogs/`) → services (`services/`) → SQLite persistence (`database/db.py`) → managed Discord channels, roles and messages.

- `bot.py`: intents, extensions, guild command synchronization and restart reconciliation. Importing it does not log in.
- `config.py`: private environment loading and portable runtime path configuration. Essential settings are validated before login.
- `database/db.py`: schema, additive initialization/migrations, transactions and application state. `data/games_seed.json` is a public game catalog with no users or server resource IDs.
- `cogs/`: commands, buttons, modals and event listeners; authorization is rechecked on actions.
- `services/`: business rules, permissions, resource identity, message rendering and safe repair.
- `tools/`: offline tests/publication audit and explicitly invoked operational utilities.
- `tests/`: synthetic Discord fakes and temporary SQLite. No live data fixtures.

## Source of truth

Git is the source of truth for code. Private SQLite is the source of truth for live application state; Discord is its integration/rendered state. Resource IDs in settings and domain tables preserve identity through rename/move/restart. Reconciliation does not turn every unknown Discord resource into a managed resource. Ambiguous mappings require owner review.

A **Game** is a library/selection/role record. A **Game Area** is an optional Discord category with linked channels. Removing an area does not inherently remove the game or its role/selection. Explicit destructive maintenance retains its existing confirmation rules.

LFG persists events, participants, private invitations/share codes and time proposals. Dashboard messages render that state. Temporary Voice stores owner/room/game mappings; owner controls use bot-managed permissions and empty-room cleanup. Streamer voice/resources have their own lifecycle.

Support tickets persist metadata, creator, assignment, status and private channel/message IDs. Creator/Staff access is ticket-specific. Closure preserves history and locks creator posting; no automatic transcript export/deletion. Suggestions enter a private Staff inbox and persist review state. Runtime ticket text, user activity and invite codes belong in private storage, never Git.

Music Bots integration configures an existing dedicated role for external bots. GamerHQ does not play music or collect third-party music credentials. Affiliate URLs and their disclosure are intentionally public content in `services/support_service.py`.

## Operations boundaries

Owner setup previews/repairs established server structure; health is read-only. Startup does synchronize commands and reconcile known resources, so a real start is not an offline check. Deploy one instance, separate code from runtime data and back up SQLite. Docker keeps the public seed under `/app/data` and mounts private SQLite under `/app/runtime/data` so the seed remains visible.
