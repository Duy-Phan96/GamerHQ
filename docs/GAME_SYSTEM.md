# Game library and optional game areas

The games table in [database/db.py](../database/db.py) is runtime authority. data/games_seed.json supplies a public starter catalog; init/seed is not a replacement for live custom games. deleted_games prevents intentionally removed catalog entries from being blindly reseeded.

## Separate concepts

- Library record: name, emoji, aliases/group, selection state and role ID.
- Game role: membership/selection can exist without any Game Area.
- Game LFG notification role: independent opt-in from Choose Games; see [role settings](ROLE_SETTINGS.md).
- Optional Game Area: category and linked chat, optional LFG, and create-voice channel IDs. area_enabled and area_has_lfg are separate from selectable.
- Legacy columns such as memes/clips remain for compatibility and cleanup; they do not imply new areas should create those channels.

Normal areas use an emoji/name role and uppercase category, 💬・chat, optional 🎯・looking-for-group and ➕・create-voice. Access is game-role gated. Music bot access is an explicit scoped exception.

## Owners and lifecycle

| Work | Entry points |
| --- | --- |
| Member selection/suggestions | cogs/games.py, /game select and /game suggest |
| Library administration | /game-admin create, rename, set-visible, delete, database and status |
| Area creation/recovery | game_service.inspect_game_structure and create_game_structure_confirmed; /game-admin setup/add-area/recover-existing |
| Batch area controls | cogs/area.py and area_management_service; /area manage |
| Conservative area removal | game_area_cleanup + game_area_safety; /game-admin remove-area and confirmed area flows |
| Selector messages | game_service.refresh_choose_games_message; existing section keys and views |
| Base non-game roles | role_service and cogs/roles.py; do not conflate with game roles |

See [command reference](COMMANDS.md) for the full current command list.

Hiding a game from selection does not mean deleting its category, role or history. Removing an area preserves the library/role unless the explicitly chosen operation says otherwise. Permanent game deletion checks dependencies and requires its own admin confirmation.

Creation uses coordination and records Discord IDs as resources are established. Existing inspection still checks role/category names and reports collisions; persisted fields and fresh DB checks must be considered before creating. Never replace an ambiguous or partially created area with another category just to finish a retry.

Removal revalidates the preview and resource dependencies; active events, occupied voice, uncertain ownership/content or new changes can block it. Unknown channels and old memes links are review evidence, not permission to delete. Startup reports legacy links rather than performing that cleanup.

## Tests

Use test_voice_area.py, test_music_cleanup.py and test_repository_safety.py for area lifecycle, permissions and fresh/seeded storage; test_lobby_management.py for event dependencies. Cover selection-vs-area separation, double creation, stale previews, hidden games, failed Discord operations and preserved roles/history.
