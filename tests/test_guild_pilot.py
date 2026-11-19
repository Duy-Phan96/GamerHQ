import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from unittest.mock import MagicMock

import discord
import pytest

from database import db, tenants
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from services.authorization_service import authorized
from services.discord_permissions import CORE_INSTALL
from services.tenant_routing import (
    GuildAdmissionError, GuildCommandTree, GuildRoutes, GuildViewStore, PILOT_ENV,
    add_shared_entry_view, event_guild, pilot_environment, pilot_routes, route_interaction, tenant_cycle,
)
from tools.staging_tenant_rehearsal import approved_binding, rehearse, retain, startup_override


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
    member = MagicMock(spec=discord.Member)
    member.guild, member.id = guild, 21
    return SimpleNamespace(client=SimpleNamespace(guild_routes=routes), guild_id=guild.id,
                           application_id=90, guild=guild, user=member,
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


def test_primary_only_fallback_checks_exact_cohort_but_parks_secondary(pilot):
    store, guilds, _ = pilot
    profile = {"stagingBotId": "90", "productionBotId": "99", "productionGuildId": "3"}
    value = pilot_environment(profile, (1, 2), primary_only=True)
    routes = pilot_routes(value, primary=1, database=store.primary_database, get_guild=guilds.get)
    assert routes.allowed == {1}
    assert routes.membership == {1, 2}
    secondary = store.tenant_directory / "2.sqlite"
    before = secondary.read_bytes()

    async def scenario():
        proof(routes)
        tree = SimpleNamespace(copy_global_to=MagicMock(), sync=AsyncMock())
        await routes.sync_commands(tree)
        assert tree.sync.await_args.kwargs["guild"].id == 1
        assert tree.sync.await_count == 1
        runtime = SimpleNamespace(restore_guild=AsyncMock())
        with pytest.raises(GuildAdmissionError):
            await routes.restore(runtime, 2)
        runtime.restore_guild.assert_not_awaited()
        with pytest.raises(GuildAdmissionError):
            with routes.interaction(interaction(routes, guilds[2])):
                pytest.fail("Parked guild must not have an interaction scope")
        with routes.scope(1):
            assert db.get_setting("customer") == "1"
        with pytest.raises(GuildAdmissionError):
            routes.verify({"id": "90", "bot": True}, [{"id": "1"}])
        with pytest.raises(GuildAdmissionError):
            with routes.scope(1):
                pytest.fail("Invalid membership must revoke even primary admission")

    asyncio.run(scenario())
    assert secondary.read_bytes() == before


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
            view = discord.ui.View()
            with routes.scope(2):
                GuildViewStore(SimpleNamespace()).add_view(view)
            assert await view._scheduled_task(None, second) == "2"
            assert await view._scheduled_task(None, first) is None
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
        with pytest.raises(GuildAdmissionError, match="require a verified tenant scope"):
            await scoped_runtime.restore_guild(guild_id=1)

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
    with pytest.raises(PermissionError):
        retain(store, tmp_path, tmp_path)
    assert not list(tmp_path.glob("tenant-pilot-*/verified.json"))


def test_backup_retention_keeps_only_completed_receipts_eligible(pilot, tmp_path):
    store, _, _ = pilot
    evidence = retain(store, tmp_path, tmp_path)
    root = tmp_path / evidence["backupBatch"]
    assert sorted(path.name for path in root.iterdir()) == ["1.sqlite", "2.sqlite", "verified.json"]
    assert all(record["restore"] == "PASS" for record in evidence["tenantRestores"])


def test_backup_budget_preserves_existing_batches_and_rejects_low_disk(pilot, tmp_path):
    store, _, _ = pilot
    retained = [tmp_path / f"tenant-pilot-existing-{index}" for index in range(14)]
    for batch in retained:
        batch.mkdir()
    with pytest.raises(PermissionError, match="retention limit"):
        retain(store, tmp_path, tmp_path)
    assert sorted(tmp_path.glob("tenant-pilot-*")) == sorted(retained)
    retained.pop().rmdir()
    with patch("tools.staging_tenant_rehearsal.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
        with pytest.raises(PermissionError, match="storage budget"):
            retain(store, tmp_path, tmp_path)
    assert sorted(tmp_path.glob("tenant-pilot-*")) == sorted(retained)


def test_secondary_selector_cannot_read_primary_configuration_or_mappings(pilot):
    from services import game_service

    store, _, _ = pilot
    with patch.object(game_service.config, "GUILD_ID", 1), patch.object(game_service, "CHOOSE_GAMES_CHANNEL_ID", 111):
        with store.scope(1):
            assert game_service.choose_games_channel_id(1) == 111
        with store.scope(2):
            assert game_service.choose_games_channel_id(2) == 0
            db.set_setting("managed_channel:2:choose-your-games", "222")
            assert game_service.choose_games_channel_id(2) == 222
            with pytest.raises(PermissionError, match="another guild"):
                game_service.choose_games_channel_id(1)


def test_uncached_owner_envelope_and_modal_use_same_verified_guild(pilot):
    _, guilds, routes = pilot
    guild = guilds[2]
    guild.owner_id = 21
    guild.get_member = lambda _: None
    event = interaction(routes, guild)

    async def submit(interaction, components, resolved):
        assert authorized(guild, interaction.user)
        return db.get_setting("customer")

    async def scenario():
        proof(routes)
        assert not authorized(guild, event.user)
        with patch("discord.ui.Modal._scheduled_task", side_effect=submit):
            modal = discord.ui.Modal(title="Owner setup")
            with routes.scope(2):
                GuildViewStore(SimpleNamespace()).add_view(modal)
            assert await modal._scheduled_task(event, [], {}) == "2"
            event.user.guild = guilds[1]
            assert await modal._scheduled_task(event, [], {}) is None
            event.response.send_message.assert_awaited_once()

    asyncio.run(scenario())


def test_gateway_modal_constructs_a_real_uncached_owner_member(pilot):
    store, _, _ = pilot
    from bot import GamerHQBot

    def user(user_id, *, bot=False):
        return {"id": str(user_id), "username": "fixture", "discriminator": "0",
                "avatar": None, "bot": bot}

    async def scenario():
        bot = GamerHQBot()
        state = bot._connection
        state.user = discord.ClientUser(state=state, data=user(90, bot=True))
        guild = discord.Guild(state=state, data={"id": "2", "name": "Fixture", "owner_id": "21",
                                                 "roles": [], "members": []})
        guild._add_member(discord.Member(state=state, guild=guild, data={
            "user": user(90, bot=True), "roles": [], "flags": 0,
        }))
        state._add_guild(guild)
        routes = GuildRoutes(application_id=90, store=store, get_guild=bot.get_guild)
        bot.guild_routes = routes
        proof(routes)
        done = asyncio.get_running_loop().create_future()

        class OwnerModal(discord.ui.Modal, title="Tenant owner"):
            async def on_submit(self, interaction):
                done.set_result((authorized(guild, interaction.user), db.get_setting("customer")))

        modal = OwnerModal()
        with routes.scope(2):
            state.store_view(modal)
        assert guild.get_member(21) is None
        data = {"id": "100", "type": 5, "token": "fixture", "version": 1, "application_id": "90",
                "guild_id": "2", "attachment_size_limit": 1024,
                "member": {"user": user(21), "roles": [], "flags": 0, "permissions": "0"},
                "data": {"custom_id": modal.custom_id, "components": []}}
        with patch.object(state, 'dispatch'):
            state.parse_interaction_create(data)
            assert await asyncio.wait_for(done, 1) == (True, "2")
            state.parse_interaction_create({**data, "guild_id": "3"})
            assert state._get_guild(3) is None
        assert guild.get_member(21) is None

    asyncio.run(scenario())


def test_bot_channel_lookups_hold_cross_tenant_and_unbound_results(pilot):
    from bot import GamerHQBot

    _, guilds, routes = pilot
    bot = GamerHQBot()
    channel = SimpleNamespace(guild=guilds[1])

    async def verify():
        with (
            patch.object(discord.Client, 'get_channel', return_value=channel),
            patch.object(discord.Client, 'fetch_channel', new_callable=AsyncMock, return_value=channel) as fetch,
        ):
            assert bot.get_channel(99) is channel
            assert await bot.fetch_channel(99) is channel
            bot.guild_routes = routes
            proof(routes)
            assert bot.get_channel(99) is None
            with pytest.raises(GuildAdmissionError, match='scope'):
                await bot.fetch_channel(99)
            assert fetch.await_count == 1
            with routes.scope(2):
                assert bot.get_channel(99) is None
                with pytest.raises(GuildAdmissionError, match='does not belong'):
                    await bot.fetch_channel(99)
            with routes.scope(1):
                assert bot.get_channel(99) is channel
                assert await bot.fetch_channel(99) is channel

    asyncio.run(verify())


def test_revoke_cancels_in_flight_work_and_rolls_back_uncommitted_sql(pilot):
    store, guilds, routes = pilot

    async def scenario():
        proof(routes)
        entered = asyncio.Event()
        event = interaction(routes, guilds[2])

        async def write():
            with db.connect() as conn:
                conn.execute("UPDATE settings SET value='unsafe' WHERE key='customer'")
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    pass   # Even a callback that handles cancellation cannot commit.

        task = asyncio.create_task(route_interaction(event, write))
        await entered.wait()
        routes.invalidate()
        with pytest.raises(GuildAdmissionError, match="expired"):
            await task
        with store.scope(2):
            assert db.get_setting("customer") == "2"

    asyncio.run(scenario())


def test_authenticated_refresh_and_scoped_command_registration(pilot):
    _, guilds, routes = pilot
    http = SimpleNamespace(request=AsyncMock(side_effect=[{"id": "90", "bot": True}, [{"id": "1"}, {"id": "2"}]]))
    tree = SimpleNamespace(copy_global_to=MagicMock(), sync=AsyncMock())

    async def scenario():
        await routes.refresh(http)
        await routes.sync_commands(tree)
        assert [call.kwargs["guild"].id for call in tree.sync.await_args_list] == [1, 2]
        assert [call.args[0].path for call in http.request.await_args_list] == ["/users/@me", "/users/@me/guilds"]

    asyncio.run(scenario())
    assert event_guild("on_member_update", [SimpleNamespace(guild=guilds[1]), SimpleNamespace(guild=guilds[2])]) is None
    assert event_guild("on_raw_message_delete", [SimpleNamespace(guild_id=2)]) == 2
    assert event_guild("on_ready", []) is None


def test_membership_refresh_cannot_republish_proof_after_invalidation(pilot):
    _, _, routes = pilot

    async def scenario():
        pending = asyncio.Event()
        release = asyncio.Event()

        async def request(route, **kwargs):
            if route.path == "/users/@me":
                return {"id": "90", "bot": True}
            pending.set()
            await release.wait()
            return [{"id": "1"}, {"id": "2"}]

        check = asyncio.create_task(routes.refresh(SimpleNamespace(request=request)))
        await pending.wait()
        routes.invalidate()
        release.set()
        with pytest.raises(GuildAdmissionError, match="changed during verification"):
            await check
        with pytest.raises(GuildAdmissionError):
            with routes.scope(1):
                pytest.fail("An expired REST response cannot admit either guild")

    asyncio.run(scenario())


def test_existing_cog_cycles_and_retries_use_fresh_scopes(pilot):
    _, _, routes = pilot
    seen = []
    cog = SimpleNamespace(bot=SimpleNamespace(guild_routes=routes))

    @tenant_cycle
    async def tick(_):
        seen.append(db.get_setting("customer"))

    async def retry():
        seen.append(db.get_setting("customer"))

    async def scenario():
        proof(routes)
        await tick(cog)
        assert sorted(seen) == ["1", "2"]
        with routes.scope(2):
            delayed = routes.create_retry(2, retry)
        await delayed
        assert seen[-1] == "2"
        with routes.scope(1):
            delayed = routes.create_retry(1, retry)
        routes.invalidate()
        await delayed
        assert len(seen) == 3

    asyncio.run(scenario())


def test_controller_binding_requires_exact_protected_non_production_pair():
    pin = {"applicationId": "90", "guildIds": ["1", "2"]}
    profile = {"environment": "staging", "stagingBotId": "90", "stagingGuildId": "1",
               "productionBotId": "99", "productionGuildId": "3", "tenantPilot": pin}
    assert approved_binding(profile, {"tenantPilot": pin}) == (1, 2)
    with pytest.raises(PermissionError):
        approved_binding(profile, {})
    wrong = {**pin, "guildIds": ["1", "3"]}
    with pytest.raises(PermissionError):
        approved_binding({**profile, "tenantPilot": wrong}, {"tenantPilot": wrong})


def test_protected_startup_checks_every_guild_before_touching_application(pilot):
    from bot import GamerHQBot

    store, guilds, _ = pilot
    pin = {"applicationId": "90", "guildIds": ["1", "2"]}
    profile = {"environment": "staging", "stagingBotId": "90", "stagingGuildId": "1", "productionBotId": "99",
               "productionGuildId": "3", "tenantPilot": pin, "service": "gamerhq-staging", "database": store.primary_database}
    value = startup_override(profile, {"tenantPilot": pin}, "sha256:" + "a" * 64)["services"]["gamerhq-staging"]["environment"][PILOT_ENV]
    bot = GamerHQBot()
    bot.guild_routes = pilot_routes(value, primary=1, database=store.primary_database, get_guild=guilds.get)
    core = discord.Permissions(**dict.fromkeys(CORE_INSTALL, True))

    async def read(route, **_):
        guild = str(route.guild_id)
        if route.path == "/users/@me":
            return {"id": "90", "bot": True}
        if route.path == "/users/@me/guilds":
            return [{"id": "1"}, {"id": "2"}]
        if route.path.endswith("/roles"):
            return [{"id": guild, "permissions": str(core.value)}]
        if "/members/" in route.path:
            return {"roles": [], "user": {"id": "90"}}
        assert route.path == "/guilds/{guild_id}"
        return {"id": guild, "owner_id": "21"}

    async def prepare():
        assert db.get_setting("customer") == "1"

    async def verify():
        with patch.object(bot.http, "request", side_effect=read), patch.object(bot, "_prepare_application", side_effect=prepare) as app:
            core.pin_messages = False
            with pytest.raises(GuildAdmissionError, match="Pin Messages"):
                await bot.setup_hook()
            app.assert_not_awaited()
            core.pin_messages = True
            await bot.setup_hook()
            app.assert_awaited_once()
            assert bot.guild_routes.accepting is False

    asyncio.run(verify())


def test_discord_client_rejects_unbound_cross_guild_and_revoked_resource_writes(pilot):
    from discord.http import Route

    _, guilds, routes = pilot
    request = AsyncMock(return_value={"id": "200", "guild_id": "2"})
    write = Route("PATCH", "/channels/{channel_id}", channel_id=200)
    cached = SimpleNamespace(guild=guilds[2])

    async def verify():
        proof(routes)
        with pytest.raises(GuildAdmissionError):
            await routes.discord_request(request, write, lambda _: cached)
        with routes.scope(1), pytest.raises(GuildAdmissionError, match="another guild"):
            await routes.discord_request(request, write, lambda _: cached)
        request.assert_not_awaited()
        with routes.scope(2):
            await routes.discord_request(request, write, lambda _: None)
            assert [call.args[0].method for call in request.await_args_list] == ["GET", "PATCH"]
            routes.invalidate()
            with pytest.raises(GuildAdmissionError):
                await routes.discord_request(request, write, lambda _: cached)
        assert request.await_count == 2

    asyncio.run(verify())


def test_pilot_blocks_unbound_database_fallback_and_routes_shared_entry(pilot, monkeypatch):
    _, guilds, routes = pilot
    monkeypatch.setattr(db, "tenant_scope_required", True)
    with pytest.raises(PermissionError, match="verified guild scope"):
        db.get_setting("customer")

    async def read(_, __):
        return db.get_setting("customer")

    async def verify():
        proof(routes)
        store = GuildViewStore(SimpleNamespace())
        bot = SimpleNamespace(guild_routes=routes, add_view=store.add_view)
        with patch("discord.ui.View._scheduled_task", side_effect=read):
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(custom_id="fixture"))
            with routes.scope(1):
                add_shared_entry_view(bot, view)
            assert await view._scheduled_task(None, interaction(routes, guilds[1])) == "1"
            assert await view._scheduled_task(None, interaction(routes, guilds[2])) == "2"

    asyncio.run(verify())


def test_protected_controller_seals_both_live_snapshot_probes_only_on_success(pilot, tmp_path, monkeypatch):
    import hashlib
    import json
    from tools.staging_automation.backend import HostBackend
    from tools.staging_automation.bridge import execute
    from tools.staging_automation.policy import Denied
    from hosts.gamerhq import skill_staging

    store, _, _ = pilot
    pin = {"applicationId": "90", "guildIds": ["1", "2"]}
    policy = {"tenantPilot": pin}
    profile = {"environment": "staging", "stagingBotId": "90", "stagingGuildId": "1", "productionBotId": "99",
               "productionGuildId": "3", "tenantPilot": pin, "service": "gamerhq-staging", "database": store.primary_database,
               "backupRoot": tmp_path, "workRoot": tmp_path}
    image = "sha256:" + "a" * 64
    state = {"enabled": True, "bootstrapVerified": True, "approvedImage": image}
    backend = HostBackend(profile, policy)
    monkeypatch.setattr(backend, "policy_integrity", lambda: None)
    monkeypatch.setattr(backend, "health", lambda: {"state": "HOST_READY"})
    monkeypatch.setattr(backend, "inspect", lambda: {"Id": "b" * 64, "Image": image,
                        "State": {"Running": True, "Health": {"Status": "healthy"}},
                        "Config": {"Labels": {"org.opencontainers.image.revision": "1" * 40}}})
    monkeypatch.setattr(backend, "run", MagicMock(return_value=''))
    verify = MagicMock(return_value={"verified": True, "gatewayStarted": False})
    monkeypatch.setattr(skill_staging, "verify_remote", verify)

    def preview(now):
        nonce = f"nonce-{now}"
        state["challenge"] = {"nonce": nonce, "operation": "tenant-pilot-preview", "expires": now + 60}
        return execute("tenant-pilot-preview", {"token": "fixture", "challenge": nonce}, policy, state, backend,
                       lambda _: None, verifier=lambda *args, **kwargs: {"jti": nonce, "exp": now + 60, "run_id": str(now)}, now=now)

    primary_bytes = store.primary_database.read_bytes()
    secondary_path = store.tenant_directory / "2.sqlite"
    secondary_bytes = secondary_path.read_bytes()
    from tools import staging_tenant_rehearsal as rehearsal

    def local_capture(container, guild_id, destination):
        assert container == "b" * 64

        def run(args, *, stdout, **kwargs):
            assert args[0:5] == ["docker", "exec", "--user", "10001:10001", container]
            assert args[-1] == str(guild_id)
            rehearsal.snapshot_bytes(store.tenant_directory, guild_id, 16 * 1024**2, stdout)

        capture(container, guild_id, destination, run=run)

    capture = rehearsal.capture_container_tenant
    monkeypatch.setattr(rehearsal, "capture_container_tenant", local_capture)
    evidence = preview(1000)
    assert evidence["state"] == "PILOT_PREVIEW_VERIFIED"
    assert evidence["rollbackRehearsal"] == "PRIMARY_ONLY_PASS"
    assert verify.call_args.kwargs["approved_pilot"] == (1, 2)
    receipt = tmp_path / evidence["backupBatch"] / "verified.json"
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == state["tenantPilotPreview"]["backupReceiptSha256"]
    assert [row["guildId"] for row in json.loads(receipt.read_text())["tenants"]] == [1, 2]
    assert store.primary_database.read_bytes() == primary_bytes
    assert secondary_path.read_bytes() == secondary_bytes

    secondary_path.unlink()
    with pytest.raises(Denied, match="PILOT_SECONDARY_BACKUP_REJECTED"):
        preview(1001)
    assert "tenantPilotPreview" not in state
    assert list(tmp_path.glob("tenant-pilot-*/verified.json")) == [receipt]
    assert store.primary_database.read_bytes() == primary_bytes


def test_container_snapshot_rejects_wrong_ownership_marker_and_oversize(pilot):
    import io
    import sqlite3
    from tools.staging_tenant_rehearsal import snapshot_bytes

    store, _, _ = pilot
    path = store.tenant_directory / "2.sqlite"
    output = io.BytesIO()
    with pytest.raises(ValueError, match="size budget"):
        snapshot_bytes(path.parent, 2, 1, output)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE gamerhq_tenant_identity SET guild_id=3")
    with pytest.raises(PermissionError, match="Unexpected tenant identity"):
        snapshot_bytes(path.parent, 2, 16 * 1024**2, output)
    assert output.getvalue() == b""


def test_internal_web_api_binds_the_authorized_guild_before_skill_access(pilot):
    from aiohttp.test_utils import TestClient, TestServer
    from hosts.gamerhq.web_api import create_web_api_app

    _, _, routes = pilot

    async def skill(**kwargs):
        assert kwargs["guild_id"] == tenants.current().guild_id
        return {"customer": db.get_setting("customer")}

    service = SimpleNamespace(bot=SimpleNamespace(guild_routes=routes), get_skill=AsyncMock(side_effect=skill))
    headers = {"Authorization": "Bearer fixture", "X-GamerHQ-Authorized-Guild-Ids": "2,3"}

    async def verify():
        proof(routes)
        app = create_web_api_app(platform_service=service, shared_secret="fixture")
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/v1/servers/2/skills/demo")).status == 401
            for guild_id in (1, 3):
                assert (await client.get(f"/api/v1/servers/{guild_id}/skills/demo", headers=headers)).status == 403
            response = await client.get("/api/v1/servers/2/skills/demo", headers=headers)
            assert await response.json() == {"customer": "2"}
            routes.invalidate()
            assert (await client.get("/api/v1/servers/2/skills/demo", headers=headers)).status == 403
            service.get_skill.assert_awaited_once()

    asyncio.run(verify())


def test_primary_only_watchdog_request_reads_real_event_manager_state(pilot, monkeypatch):
    from aiohttp.test_utils import TestServer
    from hosts.gamerhq.skill_packages import load_external_skill_packages
    from hosts.gamerhq.web_api import create_web_api_app
    from hosts.gamerhq.web_platform import GamerHQWebPlatformService
    from tools.skill_staging import host_request
    from urllib.error import HTTPError
    from importlib.metadata import version, PackageNotFoundError

    try:
        if version("gamerhq-skill-event-manager") != "0.1.2":
            pytest.skip("Exact staging Skill package required")
    except PackageNotFoundError:
        pytest.skip("Exact staging Skill package required")
    store, guilds, _ = pilot
    profile = {"stagingBotId": "90", "productionBotId": "99", "productionGuildId": "3"}
    value = pilot_environment(profile, (1, 2), primary_only=True)
    routes = pilot_routes(value, primary=1, database=store.primary_database, get_guild=guilds.get)
    bot = SimpleNamespace(guild_routes=routes, get_guild=guilds.get)
    runtime = GamerHQSkillRuntime(bot)
    assert load_external_skill_packages(runtime, ('event-manager',)).loaded == ('event-manager',)
    with store.scope(1), db.connect() as conn:
        conn.execute("INSERT INTO skill_guild_state VALUES (1, 'event-manager', 1, 1, 0, '0.1.1', 0)")
    before = store.primary_database.read_bytes()
    secondary = store.tenant_directory / "2.sqlite"
    secondary_before = secondary.read_bytes()
    monkeypatch.setenv("TEST_PILOT_HEALTH_SECRET", "fixture")

    async def verify():
        proof(routes)
        app = create_web_api_app(platform_service=GamerHQWebPlatformService(bot=bot, skill_runtime=runtime),
                                 shared_secret="fixture")
        async with TestServer(app) as server:
            worker = {'healthUrl': str(server.make_url('/')).rstrip('/'), 'stagingGuildId': '1',
                      'healthSecretEnv': 'TEST_PILOT_HEALTH_SECRET'}
            result = await asyncio.to_thread(host_request, worker, '/api/v1/servers/1/skills/event-manager')
            assert result['version'] == '0.1.2'
            assert result['state']['installed'] and result['state']['configured']
            assert not result['state']['enabled'] and not result['missingCapabilities']
            with pytest.raises(HTTPError) as denied:
                await asyncio.to_thread(host_request, {**worker, 'stagingGuildId': '2'},
                                        '/api/v1/servers/2/skills/event-manager')
            assert denied.value.code == 403

    asyncio.run(verify())
    assert store.primary_database.read_bytes() == before
    assert secondary.read_bytes() == secondary_before


def test_host_api_listener_does_not_inherit_expired_startup_tenant_scope(pilot, monkeypatch):
    from hosts.gamerhq.web_api import GamerHQWebApiServer
    from tools.skill_staging import host_request
    from urllib.error import HTTPError

    store, guilds, routes = pilot
    bot = SimpleNamespace(guild_routes=routes, get_guild=guilds.get)
    before = store.primary_database.read_bytes(), (store.tenant_directory / '2.sqlite').read_bytes()
    monkeypatch.setenv('TEST_PILOT_API_KEY', 'fixture')

    async def verify():
        proof(routes)
        server = GamerHQWebApiServer(bot=bot, skill_runtime=None, shared_secret='fixture', port=0)
        async def status(**kwargs):
            assert tenants.current().guild_id == kwargs['guild_id']
            return {'version': '0.1.2', 'state': {'installed': True, 'configured': True, 'enabled': False,
                                                'healthy': False}, 'missingCapabilities': []}
        server.app['platform_service'] = SimpleNamespace(bot=bot, get_skill=status)
        # Match setup_hook: listener starts while the primary bootstrap scope
        # is active, then that scope expires before Gateway READY verification.
        with routes.bootstrap_scope():
            await server.start()
        proof(routes)
        try:
            profile = {'healthUrl': 'http://127.0.0.1:' + str(server._runner.addresses[0][1]),
                       'healthSecretEnv': 'TEST_PILOT_API_KEY', 'stagingGuildId': '1'}
            result = await asyncio.to_thread(host_request, profile, '/api/v1/servers/1/skills/event-manager')
            assert result['version'] == '0.1.2' and result['state']['enabled'] is False
            routes.invalidate()
            with pytest.raises(HTTPError) as error:
                await asyncio.to_thread(host_request, profile, '/api/v1/servers/1/skills/event-manager')
            assert error.value.code == 403
        finally:
            await server.close()

    asyncio.run(verify())
    assert (store.primary_database.read_bytes(), (store.tenant_directory / '2.sqlite').read_bytes()) == before
