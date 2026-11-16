import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from database import db, tenants
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from services.response_service import SafeView
from services.tenant_routing import GuildAdmissionError, GuildCommandTree, GuildRoutes, route_interaction
from tools.staging_tenant_rehearsal import rehearse


@pytest.fixture
def pilot(tmp_path):
    primary = tmp_path / "primary.sqlite"
    store = tenants.GuildTenantStore(primary_guild=1, primary_database=primary,
                                    tenant_directory=tmp_path / "tenants", additional_guilds=(2,))
    with patch.object(db, "DB_PATH", primary):
        db.init_db()
    store.provision(2)
    for guild_id in (1, 2):
        with store.scope(guild_id):
            db.set_setting("customer", str(guild_id))
    member = SimpleNamespace(id=21)
    guilds = {guild_id: SimpleNamespace(id=guild_id, me=SimpleNamespace(id=90),
                                      get_member={21: member}.get) for guild_id in (1, 2)}
    routes = GuildRoutes(application_id=90, store=store, get_guild=guilds.get)
    return store, guilds, routes


def proof(routes):
    routes.verify({"id": "90", "bot": True}, [{"id": "1"}, {"id": "2"}])


def interaction(routes, guild):
    return SimpleNamespace(client=SimpleNamespace(guild_routes=routes), guild_id=guild.id,
                           application_id=90, guild=guild, user=SimpleNamespace(id=21),
                           response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))


def test_pilot_rejects_missing_extra_duplicate_and_wrong_bot_memberships(pilot):
    _, _, routes = pilot
    for member_ids in (["1"], ["1", "2", "3"], ["1", "1"], ["1", "3"]):
        proof(routes)
        with pytest.raises(GuildAdmissionError):
            routes.verify({"id": "90", "bot": True}, [{"id": value} for value in member_ids])
        with pytest.raises(GuildAdmissionError):
            with routes.scope(1):
                pytest.fail("Failed proof must invalidate admission")
    with pytest.raises(GuildAdmissionError):
        routes.verify({"id": "91", "bot": True}, [{"id": "1"}, {"id": "2"}])


def test_routes_scope_commands_views_and_revoke_delayed_operations(pilot):
    _, guilds, routes = pilot
    first, second = (interaction(routes, guilds[guild_id]) for guild_id in (1, 2))
    spoof = interaction(routes, guilds[1])
    spoof.guild_id = 2
    blocked = AsyncMock()

    async def see_customer(_):
        return db.get_setting("customer")

    async def on_view(_, event):
        return await see_customer(event)

    async def revoke():
        routes.invalidate()
        with pytest.raises(GuildAdmissionError):
            db.get_setting("customer")

    async def scenario():
        proof(routes)
        with patch("discord.app_commands.CommandTree._call", side_effect=see_customer):
            command_tree = object.__new__(GuildCommandTree)
            assert await command_tree._call(first) == "1"
        with patch("discord.ui.View._scheduled_task", side_effect=on_view):
            assert await SafeView()._scheduled_task(None, second) == "2"
        await route_interaction(spoof, blocked)
        spoof.response.send_message.assert_awaited_once()
        blocked.assert_not_awaited()
        await route_interaction(second, revoke)
        await route_interaction(second, blocked)
        blocked.assert_not_awaited()
        proof(routes)
        assert await route_interaction(second, see_customer, second) == "2"

    asyncio.run(scenario())


def test_restore_and_scheduler_scope_each_guild_and_keep_legacy_dispatch_unbound(pilot):
    _, guilds, routes = pilot

    async def start(*, guild_id):
        return tenants.current().guild_id, guild_id

    async def tick():
        return db.get_setting("customer")

    async def scenario():
        proof(routes)
        runtime = SimpleNamespace(restore_guild=start, scheduler=SimpleNamespace(run_due=tick))
        assert await asyncio.gather(*(routes.restore(runtime, guild_id) for guild_id in (1, 2))) == [(1, 1), (2, 2)]
        assert await asyncio.gather(*(routes.run_due(runtime, guild_id) for guild_id in (1, 2))) == ["1", "2"]
        legacy = SimpleNamespace(client=SimpleNamespace())
        assert await route_interaction(legacy, AsyncMock(return_value="legacy")) == "legacy"
        assert tenants.current() is None
        scoped_runtime = GamerHQSkillRuntime(SimpleNamespace(guild_routes=routes, get_guild=guilds.get))
        with pytest.raises(GuildAdmissionError, match="require a verified tenant scope"):
            await scoped_runtime.restore_guild(guild_id=2)
        with pytest.raises(RuntimeError, match="scoped guild routes"):
            await scoped_runtime.start_scheduler()

    asyncio.run(scenario())


def test_backup_restores_both_guilds_and_uses_the_legacy_primary_unchanged(pilot, tmp_path):
    store, _, _ = pilot
    files = [store.primary_database, store.tenant_directory / "2.sqlite"]
    original = {path: path.read_bytes() for path in files}
    proof_rows = rehearse(store, tmp_path)
    assert [(row["guildId"], row["restore"]) for row in proof_rows] == [(1, "PASS"), (2, "PASS")]
    assert len({row["backupSha256"] for row in proof_rows}) == 2
    assert {path: path.read_bytes() for path in files} == original
    rollback = tenants.GuildTenantStore(primary_guild=1, primary_database=store.primary_database,
                                       tenant_directory=store.tenant_directory)
    with rollback.scope(1):
        assert db.get_setting("customer") == "1"
    with pytest.raises(PermissionError):
        with rollback.scope(2):
            pytest.fail("Legacy rollback cannot admit the secondary guild")


def test_backup_rehearsal_rejects_incomplete_cohort(pilot, tmp_path):
    store, _, _ = pilot
    (store.tenant_directory / "2.sqlite").unlink()
    primary = store.primary_database.read_bytes()
    with pytest.raises(PermissionError, match="not been provisioned"):
        rehearse(store, tmp_path)
    assert store.primary_database.read_bytes() == primary
