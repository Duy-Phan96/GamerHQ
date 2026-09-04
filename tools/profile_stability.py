"""Offline DB timings and selector request counts. Never logs in to Discord.

Run from the project root: python -m tools.profile_stability
Optional: --baseline-code backups/rc1-stability-code.zip
The runtime DB is opened read-only and all benchmark writes use a temporary copy.
"""
import argparse
import asyncio
import json
import sqlite3
import statistics
import tempfile
import time
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from config import DB_PATH, DISPLAY_GROUP_ORDER
from database import db
from cogs.games import ChooseGamesButtons, GameCategoryView
from services import game_service


def benchmark(call, repetitions=100):
    durations = []
    for index in range(repetitions):
        start = time.perf_counter()
        call(index)
        durations.append((time.perf_counter() - start) * 1000)
    return {"median_ms": round(statistics.median(durations), 3),
            "max_ms": round(max(durations), 3), "samples": repetitions}


async def selector_counts(service):
    """11 existing pinned sections, one newly added game; no HTTP requests."""
    counts = {"history": 0, "fetch": 0, "edit": 0, "send": 0, "delete": 0}
    games = [{"id": index + 1, "name": f"Game {index}", "emoji": "🎮",
              "role_id": index + 100, "display_group": group}
             for index, group in enumerate(DISPLAY_GROUP_ORDER)]

    async def edit(**kwargs):
        counts["edit"] += 1

    def message(mid, content, view):
        return SimpleNamespace(id=mid, content=content, author=SimpleNamespace(id=99),
                               pinned=True, embeds=[], edit=edit,
                               components=[SimpleNamespace(to_dict=lambda data=data: data)
                                           for data in view.to_components()])

    intro = message(1, service.build_choose_games_message(), ChooseGamesButtons())
    sections = [("## " + game["display_group"], [game]) for game in games]
    messages = [intro] + [message(index + 2, title, GameCategoryView(items))
                          for index, (title, items) in enumerate(sections)]
    sections[0] = (sections[0][0], [games[0], {**games[0], "id": 999, "name": "New Game"}])
    stored_ids = {service._choose_games_section_key(title): index + 2
                  for index, (title, _) in enumerate(sections)}
    settings = {"choose_games_message_id": "1", "choose_games_section_message_ids": json.dumps(stored_ids)}
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild.me.id = 99

    async def history(**kwargs):
        counts["history"] += 1
        for item in messages:
            yield item

    async def fetch(mid):
        counts["fetch"] += 1
        return next(item for item in messages if item.id == mid)

    channel.history = history
    channel.fetch_message = fetch
    channel.send = AsyncMock()
    with patch.object(service, "CHOOSE_GAMES_CHANNEL_ID", 1), patch.object(
        db, "get_setting", side_effect=settings.get
    ), patch.object(db, "set_setting"), patch.object(service, "build_choose_games_sections", return_value=sections), patch.object(
        service, "cleanup_pin_system_messages", new_callable=AsyncMock
    ):
        await service.refresh_choose_games_message(SimpleNamespace(get_channel=lambda _: channel),
                                                   view=GameCategoryView, intro_view=ChooseGamesButtons)
    counts["send"] = channel.send.await_count
    # Pin-notice cleanup is common to both implementations, stubbed above.
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-code", type=Path)
    args = parser.parse_args()
    result = {"python_discord_version": discord.__version__}
    if not DB_PATH.is_file():
        raise SystemExit("Runtime DB does not exist; no database was created.")
    with tempfile.TemporaryDirectory() as folder:
        copy_path = Path(folder) / "profile.db"
        source = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(copy_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with patch.object(db, "DB_PATH", copy_path):
            games = db.get_all_games()
            result["copied_game_counts"] = {"all": len(games),
                                            "selectable": sum(bool(g["selectable"]) for g in games),
                                            "areas": sum(bool(g["area_enabled"]) for g in games)}
            result["db_all_games"] = benchmark(lambda _: db.get_all_games())
            result["db_area_games"] = benchmark(lambda _: db.get_area_games(lfg_only=True))
            if games:
                result["db_name_lookup"] = benchmark(lambda _: db.get_game_by_name(games[0]["name"]))

            def create(index):
                game = db.upsert_custom_game(f"Profile-{time.time_ns()}-{index}", "🎮", DISPLAY_GROUP_ORDER[0])
                db.set_game_role(game["id"], 123)
                db.set_game_selectable(game["id"], True)
                db.get_game_by_id(game["id"])

            result["db_create_sequence_without_discord"] = benchmark(create, repetitions=20)
            guild = SimpleNamespace(roles=[SimpleNamespace(name=f"Role {i}") for i in range(250)], categories=[])
            result["cached_structure_scan_250_roles"] = benchmark(
                lambda _: game_service.inspect_game_structure(guild, {"name": "Profile", "emoji": "🎮", "area_has_lfg": 1}))
    if args.baseline_code:
        with zipfile.ZipFile(args.baseline_code) as archive:
            source = archive.read("services/game_service.py").decode("utf-8-sig")
        baseline = types.ModuleType("baseline_game_service")
        exec(compile(source, "baseline_game_service.py", "exec"), baseline.__dict__)
        result["baseline_selector_mock_calls"] = asyncio.run(selector_counts(baseline))
    result["current_selector_mock_calls"] = asyncio.run(selector_counts(game_service))
    result["limits"] = "Offline copy; no live Discord latency/rate limits measured. Pin cleanup excluded from counts."
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
