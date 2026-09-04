# GamerHQ Changelog

All notable GamerHQ Bot changes are tracked here from the stable-release workflow onward.

## [1.0.0-beta.1-rc4] - 2026-09-04

### Games
- `/game-admin rename` accepts an optional `emoji` argument.
- Omitting or leaving `emoji` empty preserves the current game emoji.
- Emoji-only changes are supported by keeping the existing game name.
- Confirmed changes update and roll back the DB/catalog, seed entry, linked role, linked category and Choose Your Games consistently.

### Hosting and workflow
- Added a non-root Docker image and Compose service with persistent database and backup mounts.
- Added read-only production preflight and verified SQLite backup commands.
- Pinned direct Python dependency versions used by the tested release candidate.
- Replaced legacy overwrite-in-place deployment notes with a Git/tag-based Linux VPS deployment and rollback workflow.

## [1.0.0-beta.1-rc3] - 2026-09-04

### Stability
- Fixed an event-join regression where `Add Game & Join` refreshed Choose Your Games without component views and removed every public game button.
- Role assignment during event join no longer refreshes the selector because its content is unchanged.
- Selector refresh now rejects missing category/intro views before reading or editing Discord messages.
- Startup safely restores the current DB-driven game buttons using the required persistent views.

## [1.0.0-beta.1-rc2] - 2026-09-03

### Stability
- Fixed both event-creation entry points: the builder registered six buttons in row 4, exceeding Discord's five-button row limit before `_rebuild()` could run. Removed the unused builder Cancel button; cancellation remains in the preview.
- Game create, add-area preview and area confirmation now acknowledge interactions before database access or structure scans.
- Removed the automatic V27 channel migration from startup. Legacy game memes links are reported for staff review without deleting channels or clearing DB links.

### Performance
- Area creation no longer refreshes Choose Your Games, since selector membership is unchanged.
- Selector refresh reuses messages loaded from history and skips edits when content and components already match. Stored-ID fetch remains available for older messages.
- Added an offline profiler using a read-only snapshot of the runtime DB and mocked Discord calls. No production benchmark writes or Discord login.

### Validation and release status
- Added offline regression tests for both event entry points, builder/preview navigation, early acknowledgements and selector refresh.
- No schema change; runtime DB and `.env` remain byte-identical. Code-only rollback snapshot is stored locally under `backups/` and is excluded from releases.
- Remains a release candidate pending Discord smoke tests and hosting acceptance. See `STABILITY_REPORT.md` for measurements, remaining risks and rollback instructions.

## [1.0.0-beta.1-rc1] - 2026-09-03

### Stability
- Introduced the first release-candidate baseline for the new versioned deployment workflow.
- `Create Event` now acknowledges Discord immediately before loading the event builder, preventing interaction timeouts caused by slow work before the first response.
- `/lfg create` uses the same acknowledged builder flow.
- Final event creation now defers before creating private channels, posting event messages and sending invites.
- Event builder errors are logged and return a user-visible error instead of Discord's generic "application did not respond" message.

### Games
- Game Library, game-role visibility and dedicated Discord areas remain separate states.
- Existing custom games can be recovered from matching Discord resources without recreating them.
- Dedicated multiplayer areas use `chat`, `looking-for-group` and `create-voice`.
- Solo areas use `chat` and `create-voice`.
- Legacy per-game `memes` channels are no longer part of the desired game-area structure.

### Events
- Public and private LFG events are supported.
- Public events expose a shareable Discord event-post link.
- Private events support host-managed invite access, private event channels and participant-scoped voice access.
- Discord-only private sharing remains a Beta flow until a future GamerHQ web endpoint provides one-click event links.

### Community
- `community-commands`, `streamer-commands` and `mod-commands` are separate automatically refreshed command references.
- `tournaments` and `giveaways` replace the old community memes/clips channels with Coming Soon messaging.

### Deployment safety
- Runtime database files are not shipped in releases.
- `.env` is not shipped in releases.
- `GAMERHQ_DB_PATH` can point to a persistent database outside the application/release directory.
