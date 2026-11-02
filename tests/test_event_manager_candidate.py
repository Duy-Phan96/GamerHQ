"""Optional cross-repository contract gate; no candidate package is bundled.

Run with a reviewed Event Manager wheel installed in the test environment.
Ordinary Host CI explicitly skips this until the dependency is released/pinned.
"""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from database import db
from hosts.gamerhq.skill_packages import load_external_skill_packages
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime

pytest.importorskip("gamerhq_skill_event_manager", reason="Event Manager candidate not yet bundled")


def test_candidate_uses_public_package_lifecycle_and_scoped_management():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", Path(directory) / "test.db"):
            db.init_db()
            guilds = {1: SimpleNamespace(id=1), 2: SimpleNamespace(id=2)}
            bot = SimpleNamespace(guilds=list(guilds.values()), get_guild=guilds.get)
            runtime = GamerHQSkillRuntime(bot)
            try:
                report = load_external_skill_packages(runtime, ("event-manager",))
                assert report.loaded == ("event-manager",) and not report.unavailable
                await runtime.register_all()
                assert await runtime.install_skill(guild_id=1, skill_id="event-manager")
                assert not await runtime.install_skill(guild_id=1, skill_id="event-manager")
                payload = {"name": "Raid", "kind": "recurring", "startDate": "2030-01-01", "localTime": "19:00",
                           "timezone": "Europe/Berlin", "weekdays": [0, 2, 4], "visibility": "private", "requestId": "create-1"}
                result = await runtime.call_management(guild_id=1, skill_id="event-manager",
                    contract_id="event-manager.create.v1", payload=payload)
                event = result["event"]
                assert event["config"]["visibility"] == "private"
                await runtime.install_skill(guild_id=2, skill_id="event-manager")
                other = await runtime.call_management(guild_id=2, skill_id="event-manager",
                    contract_id="event-manager.list.v1", payload={})
                assert other["events"] == []
                assert await runtime.enable_skill(guild_id=1, skill_id="event-manager")
                context = await runtime.context(1, "event-manager")
                job = await context.scheduler.get_job(key="event-manager.materialize")
                assert job is not None
                await runtime.disable_skill(guild_id=1, skill_id="event-manager")
                assert await context.scheduler.get_job(key="event-manager.materialize") is None
                status = await runtime.status(guild_id=1, skill_id="event-manager")
                assert status.source_kind == "external"
                assert status.source_distribution == "gamerhq-skill-event-manager"
            finally:
                await runtime.close()
    asyncio.run(scenario())
