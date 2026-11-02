import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import importlib.util
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from database import db
from hosts.gamerhq.event_cutover import apply, digest, owns_events, prepare, rollback_unmodified, snapshot_for_guild
from hosts.gamerhq.skill_packages import load_external_skill_packages
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from skill_runtime import SkillManifest, SkillManagementApis, ManagementApiContract, SkillHealth


class RecordedMigrationSkill:
    """Recorded public response fixture; no private Skill dependency in Host CI."""
    manifest = SkillManifest(id="event-manager", name="Migration contract fixture", version="0.1.0", runtime_api_version="1",
        description="Synthetic recorded migration contract", author="Tests", management_apis=SkillManagementApis(
            exposes=(ManagementApiContract("event-manager.migrate-plan.v1"),)))
    async def register(self, ctx):
        async def handle(context, payload):
            plan = json.loads((Path(__file__).parent / "fixtures" / "event_manager_import.json").read_text())
            assert digest(payload["snapshot"]) == plan["sourceHash"]
            return plan
        ctx.management.expose("event-manager.migrate-plan.v1", handle)
    async def enable(self, ctx): pass
    async def disable(self, ctx): pass
    async def start(self, ctx): pass
    async def stop(self, ctx): pass
    async def health_check(self, ctx): return SkillHealth("PASS", "Fixture")



def commit(fixture, plan):
    bindings = {event["resourceKey"]: {"channelId": 100, "messageId": 200,
        "marker": f"Private GamerHQ event #{fixture.event['id']} · Private Raid", "members": {}}
        for key, event in plan["writes"].items() if key.startswith("event.v2:") and event.get("chat")}
    return apply(plan, backup_path=fixture.backup, backup_sha256=fixture.checksum, verified_resources=bindings)


@pytest.fixture
def fixture():
    with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", Path(directory) / "source.db"), patch("time.time", return_value=1234567890):
        db.init_db()
        series = db.create_lfg_event_series(guild_id=1, host_id=10, title="Private Raid", timezone_name="Europe/Berlin",
            frequency="weekly", weekdays=(), first_start_at=2000000000, occurrence_limit=4, max_players=5, invite_lead_minutes=15,
            visibility="private")
        event = db.create_lfg_event(guild_id=1, host_id=10, title="Private Raid", start_at=2000000000, max_players=5,
            invite_lead_minutes=15, visibility="private", series_id=series["id"], occurrence_index=0)
        db.set_lfg_event_member(event["id"], 20, "joined")
        db.set_lfg_event_member(event["id"], 30, "invited")
        db.set_lfg_event_private_channel(event["id"], 100)
        db.set_lfg_event_message(event["id"], channel_id=100, message_id=200)
        db.claim_lfg_voice_notification(event["id"], 20, 123)
        with db.connect() as conn:
            conn.execute("INSERT INTO lfg_time_proposals(event_id,proposer_id,start_at,reason,status,created_at) VALUES(?,?,?,?,?,?)",
                         (event["id"], 20, 2000100000, "Later", "PENDING", 123))
        guild = SimpleNamespace(id=1)
        runtime = GamerHQSkillRuntime(SimpleNamespace(guilds=[guild], get_guild=lambda ident: guild if ident == 1 else None))
        if importlib.util.find_spec("gamerhq_skill_event_manager"):
            load_external_skill_packages(runtime, ("event-manager",))
        else:
            runtime.register(RecordedMigrationSkill(), source_kind="external", source_distribution="contract-fixture")
        async def setup():
            await runtime.register_all()
            await runtime.install_skill(guild_id=1, skill_id="event-manager")
        asyncio.run(setup())
        backup = Path(directory) / "backup.db"
        with db.connect() as conn, closing(sqlite3.connect(backup)) as target:
            conn.backup(target)
        yield SimpleNamespace(runtime=runtime, event=event, backup=backup,
            checksum=hashlib.sha256(backup.read_bytes()).hexdigest())
        asyncio.run(runtime.close())


def test_cutover_preserves_six_tables_freezes_legacy_and_is_retry_safe(fixture):
    plan = asyncio.run(prepare(fixture.runtime, guild_id=1))
    with db.connect() as conn:
        before = snapshot_for_guild(conn, 1)
    assert all(before[key] for key in ("series", "events", "members", "messages", "voiceNotifications", "timeProposals"))
    assert commit(fixture, plan)
    assert not commit(fixture, plan)
    assert owns_events(1) and not owns_events(2)
    assert db.get_active_lfg_events(1) == []
    with db.connect() as conn:
        assert snapshot_for_guild(conn, 1) == before
        imported = {row["storage_key"]: json.loads(row["value_json"]) for row in conn.execute(
            "SELECT * FROM skill_storage WHERE guild_id=1 AND skill_id='event-manager'")}
    assert digest(imported) == plan["targetHash"]
    assert imported["legacy-reference.v1:" + str(fixture.event["id"])]["legacyEventId"] == fixture.event["id"]
    for key, rows in before.items():
        if isinstance(rows, list):
            assert [imported[f"legacy.v1:{key}:{index}"] for index in range(len(rows))] == rows
    with pytest.raises(sqlite3.IntegrityError, match="authority"):
        db.set_lfg_event_status(fixture.event["id"], "cancelled")
    with pytest.raises(sqlite3.IntegrityError, match="authority"):
        db.set_lfg_event_member(fixture.event["id"], 40, "joined")
    assert db.create_lfg_event(guild_id=2, host_id=10, title="Other tenant", start_at=2000000000, max_players=5, invite_lead_minutes=15)


def test_source_changes_block_cutover_without_partial_import(fixture):
    plan = asyncio.run(prepare(fixture.runtime, guild_id=1))
    db.set_lfg_event_member(fixture.event["id"], 40, "invited")
    with pytest.raises(ValueError, match="changed"):
        commit(fixture, plan)
    assert not owns_events(1)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM skill_storage WHERE skill_id='event-manager'").fetchone()[0] == 0


def test_verified_unmodified_rollback_restores_legacy_and_refuses_new_writes(fixture):
    plan = asyncio.run(prepare(fixture.runtime, guild_id=1))
    commit(fixture, plan)
    assert rollback_unmodified(guild_id=1, resources_verified=True)
    assert not owns_events(1)
    db.set_lfg_event_member(fixture.event["id"], 20, "joined")  # original value; source unchanged
    commit(fixture, plan)
    with db.connect() as conn:
        conn.execute("UPDATE skill_storage SET value_json='{}' WHERE storage_key='migration.v1'")
    with pytest.raises(ValueError, match="changed"):
        rollback_unmodified(guild_id=1, resources_verified=True)
    assert owns_events(1)


def test_namespace_conflicts_and_unverified_backups_fail_closed(fixture):
    plan = asyncio.run(prepare(fixture.runtime, guild_id=1))
    with pytest.raises(ValueError, match="backup"):
        apply(plan, backup_path=fixture.backup, backup_sha256="0" * 64)
    with db.connect() as conn:
        conn.execute("INSERT INTO skill_storage VALUES(1,'event-manager','conflict','{}',1)")
    with pytest.raises(ValueError, match="empty"):
        commit(fixture, plan)
    assert not owns_events(1)


def test_recorded_public_contract_is_valid_without_private_package_access(fixture):
    runtime = GamerHQSkillRuntime(fixture.runtime.bot)
    runtime.register(RecordedMigrationSkill(), source_kind="external", source_distribution="recorded-contract-fixture")
    async def scenario():
        try:
            await runtime.register_all()
            plan = await prepare(runtime, guild_id=1)
            assert digest(plan["writes"]) == plan["targetHash"]
            assert plan["writes"]["migration.v1"]["counts"]["voiceNotifications"] == 1
        finally:
            await runtime.close()
    asyncio.run(scenario())
