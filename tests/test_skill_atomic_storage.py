"""Atomic storage must remain safe across independent adapters and tenants."""
import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from database import db
from hosts.gamerhq.skill_host import CapabilityPermissions, GamerHQSkillStorage


@pytest.fixture
def storage_factory():
    with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", Path(directory) / "state.db"):
        db.init_db()
        yield lambda guild=1, skill="event-manager", caps=("storage.skill", "storage.atomic"): GamerHQSkillStorage(
            guild_id=guild, skill_id=skill, permissions=CapabilityPermissions(caps))


def test_atomic_claim_has_one_winner_across_connections(storage_factory):
    def claim(_):
        return asyncio.run(storage_factory().compare_and_set("claim:one", None, {"reserved": True}))
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(claim, range(16))) == 1


def test_compare_is_structural_and_rejects_stale_value(storage_factory):
    async def scenario():
        storage = storage_factory()
        assert await storage.compare_and_set("event:one", None, {"a": 1, "b": 2})
        assert await storage.compare_and_set("event:one", {"b": 2, "a": 1}, {"revision": 2})
        assert not await storage.compare_and_set("event:one", {"a": 1, "b": 2}, {"revision": 3})
        assert await storage.get("event:one") == {"revision": 2}
        assert not await storage.compare_and_set("event:one", {"revision": True}, {"revision": 3})
    asyncio.run(scenario())


def test_namespace_isolation_and_literal_prefix_pagination(storage_factory):
    async def scenario():
        for key in ("event:a", "event:b", "event:c", "eventual"):
            assert await storage_factory().compare_and_set(key, None, {"id": key})
        assert await storage_factory().list_keys(prefix="event:", limit=2) == ("event:a", "event:b")
        assert await storage_factory().list_keys(prefix="event:", after="event:b") == ("event:c",)
        for other in (storage_factory(2), storage_factory(skill="other")):
            assert await other.list_keys(prefix="event:") == ()
            assert await other.compare_and_set("event:a", None, {"private": True})
        assert await storage_factory().get("event:a") == {"id": "event:a"}
    asyncio.run(scenario())


def test_capability_size_and_json_validation(storage_factory):
    async def scenario():
        denied = storage_factory(caps=("storage.skill",))
        with pytest.raises(PermissionError):
            await denied.compare_and_set("key", None, {})
        with pytest.raises(PermissionError):
            await denied.list_keys(prefix="key")
        for value in (None, float("nan"), {"content": "x" * 65536}):
            with pytest.raises(ValueError):
                await storage_factory().compare_and_set("key", None, value)
        for limit in (0, 1001, True):
            with pytest.raises(ValueError):
                await storage_factory().list_keys(prefix="key", limit=limit)
        assert await storage_factory().get("key") is None
    asyncio.run(scenario())
