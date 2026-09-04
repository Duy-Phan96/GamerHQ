# GamerHQ stability report — 2026-09-03

Status: **1.0.0-beta.1-rc3**, not yet promoted to stable Beta. Work used only this project folder. It currently has no Git repository. No bot login, restart, deployment or Discord resource mutation was performed.

## RC3 selector regression

After the event builder fix was validated on Discord, the public category messages appeared without game buttons. The runtime DB remained intact: **47 games were selectable**, and local construction produced 2–7 buttons for every category.

The cause was the existing `Add Game & Join` path in `cogs/lfg.py`. After assigning the selected game's role, it called `refresh_choose_games_message(interaction.client)` without the category or intro views. The refresh then edited all managed Discord messages with `view=None`, removing their components. This explains why the problem appeared while testing the now-working event flow.

The role assignment no longer triggers a selector refresh because neither selector membership nor button metadata changes. Refresh now rejects missing views before accessing Discord, and `on_ready` performs an idempotent DB-driven refresh with both required persistent views. Restarting rc3 repairs the affected messages without changing game records or roles.

## Event creation: root cause and fix

No saved `.log` files were found. The original code was reproduced locally with Python 3.14.3 and the installed discord.py 2.7.1. The traceback was:

```text
cogs/lfg.py:955, EventBuilderView.__init__
    super().__init__(timeout=600)
discord/ui/view.py, View.__init__ -> _ViewWeights -> add_item
    ValueError: item would not fit at row 4 (6 > 5 width)
```

Both Create Event and `/lfg create` instantiate this same class. Six decorated buttons were assigned to row 4. discord.py builds and validates decorated components in the superclass constructor, before `_rebuild()` can remove anything. The existing exception handlers therefore displayed the generic error after successfully acknowledging the interaction. This was not a timeout or a modal failure.

Removed the unused decorated Cancel button from the builder. `_rebuild()` already excluded it. Four selects now occupy rows 0–3 and exactly five buttons occupy row 4. Cancel remains in the final preview; an unfinished ephemeral draft can also be dismissed. Public persistent custom IDs are unchanged.

## Performance findings

Measurements below use a consistent read-only backup of the configured runtime database. All benchmark writes run against a disposable temporary copy. The current copy contains **65 games, 47 selectable games and 64 enabled areas**, rather than the earlier 64/46/63 figures in the handover.

| Operation | Samples | Median | Maximum |
| --- | ---: | ---: | ---: |
| Read all games, including connection open/close | 100 | 1.335 ms | 2.774 ms |
| Read LFG area games | 100 | 0.909 ms | 2.570 ms |
| Find game by name | 100 | 0.481 ms | 1.098 ms |
| Create DB entry, set role/visibility, read back; no Discord | 20 | 44.936 ms | 303.117 ms |
| Structure scan with 250 cached roles, no categories | 100 | 0.023 ms | 0.086 ms |

SQLite is synchronous and opens a connection per helper call. Local reads are short; separate write commits have measurable latency and can block the event loop. These measurements do not establish behavior under production lock contention or VPS disk load. No database architecture rewrite was made in this patch.

The original selector refresh fetched and edited every section, even when unchanged. A controlled comparison against the saved original code used 11 existing pinned category sections plus an intro, adding one game without crossing a page boundary:

| Mock Discord operations | Original | Updated |
| --- | ---: | ---: |
| History traversal | 1 | 1 |
| Individual message fetches | 12 | 0 |
| Message edits | 12 | 1 |
| Sends / deletes | 0 / 0 | 0 / 0 |

Pin-notice cleanup is excluded from this comparison and still runs. Older messages outside the history window still require fetches; new sections require sends and pins. These are measured mock call counts, **not measured Discord network timings**.

`add-area` formerly ran the same full selector refresh after creating an area. This is now skipped because area creation preserves visibility and stable game-ID buttons. Existing checks of game identity and visibility remain in place. Resource creation itself still uses sequential Discord calls: category, chat, optional LFG and create-voice, plus a role only if missing. No full server rebuild, command sync or explicit sleep occurs in these command paths. Role/channel inspections use the guild cache, not HTTP fetches.

Create, add-area preview and confirmation now defer before DB work/scans. Real network latency and Discord rate-limit waits remain unmeasured; they require an authorized runtime test with visible API logs.

## Startup safety

The previous `on_ready` called a V27 migration that renamed community channels, deleted DB-linked game memes channels and cleared DB links. Startup now only reports the number of legacy links needing staff review. The maintenance helper remains available in source but is no longer called automatically from `bot.py`.

This is not a complete hosting/integrity audit. Existing scheduled event expiration and empty temporary voice cleanup remain unchanged. Other existing concerns to address before stable promotion:

- `init_db()` still derives `area_enabled=1` from non-null category IDs on every startup; desired-state migration should be reviewed separately.
- Event game choices are truncated to the first 25 entries; a channel-bound game beyond that slice is an existing UI edge case.
- `add-area` currently saves the LFG option before admin confirmation; this behavior predates this patch.
- No full restart test of every persistent view, private-event permission test, invitation lifecycle test or actual event-post creation test was performed.

## Changed files

- `cogs/lfg.py`: builder constructor fix.
- `cogs/games.py`: earlier acknowledgements; omit unnecessary area selector refresh.
- `services/game_service.py`: history reuse and unchanged-message comparison.
- `bot.py`: replace automatic legacy migration with a read-only report.
- `tests/test_stability.py`: offline callback and component regressions.
- `tools/profile_stability.py`: reproducible offline DB timing and selector call comparison.
- `VERSION`, `CHANGELOG.md`, `STABILITY_REPORT.md`: release-candidate documentation.

No database schema or configuration change. SHA-256 verification confirms `.env` and the existing runtime DB files remained byte-identical during this work. No runtime DB or `.env` is included in the code rollback archive.

## Verification and remaining acceptance

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tools.profile_stability
# Local original-code comparison, when the snapshot is present:
.\.venv\Scripts\python.exe -m tools.profile_stability --baseline-code backups/rc1-stability-code.zip
```

Seven test methods passed, including both button/slash paths in general and game-bound channels, no-game/missing-role handling, component serialization, title-modal opening, AM/PM, visibility, preview/edit/cancel and admin acknowledgement order. Syntax parsing passed for all 26 current Python source files. Tests mock Discord; they do not validate server-side permissions or HTTP payload acceptance.

Before promoting to `1.0.0-beta.1`, run the existing release checklist on a test guild: open both event entry points, publish a public and private test event, verify permissions/join/leave/share, restart and exercise persistent buttons, and record actual create/add-area durations. Retain the release-candidate label until those checks pass.

## Rollback

A code-only snapshot of the six changed pre-existing files is stored locally at `backups/rc1-stability-code.zip`. Stop the bot before restoring those files. Do not replace `.env`, `gamerhq.db` or the external `GAMERHQ_DB_PATH`. No DB rollback/migration is needed. Added tests, profiler and report may remain or be removed independently.

Restoring rc1 also restores its known broken event builder and automatic V27 startup migration. It is a source rollback, not a claim that rc1 is a safe production fallback. The snapshot and hash manifest are local maintenance artifacts under the already ignored `backups/` directory, not release contents.
