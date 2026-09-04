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
- [ ] Event cancellation cleans up posts/private resources once.

## Voice

- [ ] Game `create-voice` generator creates a temporary room.
- [ ] Temporary room is deleted after it becomes empty.

## Guides

- [ ] `community-commands` contains no streamer/admin commands.
- [ ] `streamer-commands` exists only in the Streamers category.
- [ ] `mod-commands` remains staff-only.
- [ ] Restart updates guides without duplicates.

## Community migration

- [ ] `tournaments` exists with Coming Soon copy.
- [ ] `giveaways` exists with Coming Soon copy.
- [ ] Old community memes/clips channels are not duplicated.
