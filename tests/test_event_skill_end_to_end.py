"""Optional installed-wheel acceptance; public Host CI never fetches private code.

Run with the reviewed Event Manager package installed. Only its public entry point,
management contracts and SDK lifecycle are used, never private repository imports.
"""
import asyncio
from datetime import datetime, timezone
import importlib.util
from importlib.metadata import version
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from packaging.version import Version
import pytest

from database import db
from hosts.gamerhq.event_cutover import owns_events
from hosts.gamerhq.skill_host import CapabilityPermissions, GamerHQSkillStorage
from hosts.gamerhq.skill_packages import load_external_skill_packages
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from hosts.gamerhq.web_platform import GamerHQWebPlatformService
from test_skill_managed_resources import fixture as resources
from test_event_authority_cutover import fixture as legacy, prepare, commit

pytestmark = pytest.mark.skipif(importlib.util.find_spec("gamerhq_skill_event_manager") is None,
    reason="Install the approved private Skill artifact for combined acceptance.")


def test_public_install_editor_resources_and_restart_share_one_authority(resources):
    async def scenario():
        guild = resources.guild
        bot = SimpleNamespace(guilds=[guild], get_guild=lambda ident: guild if ident == 1 else None)
        db.set_setting("skill_resources:1:event-manager", json.dumps({"categoryId": 10, "allowPublic": False}))
        def runtime():
            value = GamerHQSkillRuntime(bot)
            load_external_skill_packages(value, ("event-manager",))
            return value
        first = runtime()
        await first.register_all()
        await first.install_skill(guild_id=1, skill_id="event-manager")
        assert owns_events(1)
        async def call(target, operation, payload):
            return await target.call_management(guild_id=1, skill_id="event-manager",
                contract_id=f"event-manager.{operation}.v1", payload=payload, actor_id=50)
        draft = (await call(first, "describe", {}))["defaultDraft"]
        draft.update(name="Customer Raid", kind="recurring", weekdays=[0, 2, 4], visibility="private",
            members="50", voiceEnabled=False, requestId="customer-raid")
        await call(first, "validate", draft)
        created = (await call(first, "create", draft))["event"]
        assert created["ownerId"] == "50"
        await first.enable_skill(guild_id=1, skill_id="event-manager")
        await call(first, "reconcile", {"eventId": created["id"]})
        await first.close()
        second = runtime()
        await second.register_all()
        assert await second.restore_guild(guild_id=1) == ("event-manager",)
        try:
            assert (await second.status(guild_id=1, skill_id="event-manager")).health == "PASS"
            current = (await call(second, "get", {"eventId": created["id"]}))["event"]
            current.pop("id")
            current.update(eventId=created["id"], name="Updated Customer Raid", members="")
            await call(second, "validate", current)
            await call(second, "update", current)
            await call(second, "reconcile", {"eventId": created["id"]})
            await call(second, "reconcile", {"eventId": created["id"]})
            state = (await call(second, "get", {"eventId": created["id"]}))["state"]
            assert state["chat"]["channelId"] == "100"
            assert len(resources.channels) == len(resources.messages) == 1
            assert guild.create_text_channel.await_count == 1
            assert len((await call(second, "history", {"eventId": created["id"]}))["occurrences"]) > 1
            with pytest.raises(db.sqlite3.IntegrityError, match="authority"):
                db.create_lfg_event(guild_id=1, host_id=50, title="Legacy duplicate", start_at=2000000000,
                    max_players=5, invite_lead_minutes=15)
        finally:
            await second.close()
    asyncio.run(scenario())


def test_expiry_upgrade_and_reissue_preserve_sqlite_schedule_and_discord_access(resources):
    if Version(version("gamerhq-skill-event-manager")) < Version("0.1.3"):
        pytest.skip("Invitation expiry requires Event Manager 0.1.3 or later.")

    async def scenario():
        guild = resources.guild
        owner = resources.member
        guest = MagicMock(spec=discord.Member)
        guest.id, guest.guild = 60, guild
        guild.get_member.side_effect = {owner.id: owner, guest.id: guest}.get
        bot = SimpleNamespace(guilds=[guild], get_guild=lambda ident: guild if ident == guild.id else None)
        db.set_setting("skill_resources:1:event-manager", json.dumps({"categoryId": 10, "allowPublic": False}))
        storage = GamerHQSkillStorage(guild_id=1, skill_id="event-manager",
                                      permissions=CapabilityPermissions(("storage.skill",)))
        now = int(datetime(2030, 1, 7, 10, tzinfo=timezone.utc).timestamp())

        async def open_runtime():
            runtime = GamerHQSkillRuntime(bot)
            load_external_skill_packages(runtime, ("event-manager",))
            await runtime.register_all()
            bot.skill_runtime = runtime
            return runtime

        async def call(operation, payload, *, actor=50):
            return await bot.skill_runtime.call_management(
                guild_id=1, skill_id="event-manager", contract_id=f"event-manager.{operation}.v1",
                payload=payload, actor_id=actor)

        async def event_detail():
            return (await call("get", {"eventId": event_id}))["state"]

        async def inbox():
            return (await call("invitations", {}, actor=guest.id))["invitations"]

        async def accept(invite):
            return await call("invite-response", {"eventId": invite["eventId"],
                "revision": invite["revision"], "accept": True}, actor=guest.id)

        async def edit(**changes):
            current = (await call("get", {"eventId": event_id}))["event"]
            current.pop("id")
            return await call("update", {**current, "eventId": event_id, **changes})

        with patch("time.time", side_effect=lambda: now):
            runtime = await open_runtime()
            try:
                await runtime.install_skill(guild_id=1, skill_id="event-manager")
                draft = (await call("describe", {}))["defaultDraft"]
                draft.update(requestId="expiry-upgrade", name="Private recurring raid", kind="recurring",
                             startDate="2030-01-08", localTime="19:00", timezone="UTC", weekdays=[0, 2, 4],
                             visibility="private", invited=str(guest.id))
                event_id = (await call("create", draft))["event"]["id"]
                await runtime.enable_skill(guild_id=1, skill_id="event-manager")
                await call("reconcile", {"eventId": event_id})
                before = await event_detail()
                history = (await call("history", {"eventId": event_id}))["occurrences"]
                key = "event.v2:" + event_id
                old = await storage.get(key)
                # Simulate a pending row written by 0.1.2 before the Host restarts.
                old.pop("invitationTtlHours")
                old.pop("invitationExpiresAt")
                await storage.set(key, old)
            finally:
                await runtime.close()

            runtime = await open_runtime()
            try:
                assert await runtime.restore_guild(guild_id=1) == ("event-manager",)
                legacy, = await inbox()
                deadline = (await storage.get(key))["invitationExpiresAt"][str(guest.id)]
                assert deadline == now + 7 * 86400 and legacy["revision"] == before["revision"]
            finally:
                await runtime.close()

            now += 60
            runtime = await open_runtime()
            try:
                assert await runtime.restore_guild(guild_id=1) == ("event-manager",)
                assert await inbox() == [legacy]
                assert (await storage.get(key))["invitationExpiresAt"][str(guest.id)] == deadline
                assert (await event_detail())["chat"] == before["chat"]
                assert (await call("history", {"eventId": event_id}))["occurrences"] == history
                channel, = resources.channels
                assert channel.overwrites_for(guest).view_channel is None
                assert channel.overwrites_for(guild.default_role).view_channel is False

                await edit(invited="", invitationTtlHours=1)
                assert (await accept(legacy))["state"] == "resolved"
                await edit(invited=str(guest.id))
                short, = await inbox()
                now += 3600
                assert await inbox() == []
                assert (await accept(short))["state"] == "resolved"
                assert (await event_detail())["members"] == [str(owner.id)]
                assert channel.overwrites_for(guest).view_channel is None

                await edit(invited=str(guest.id))
                fresh, = await inbox()
                assert (await accept(short))["code"] == "conflict"
                assert (await accept(fresh))["state"] == "accepted"
                assert (await accept(fresh))["state"] == "resolved"
                report = await runtime.scheduler.run_due(now=now)
                assert report.executed == 1 and report.failed == 0
                assert channel.overwrites_for(guest).view_channel is True
                assert (await event_detail())["chat"] == before["chat"]
                assert [row["id"] for row in (await call("history", {"eventId": event_id}))["occurrences"]] == [
                    row["id"] for row in history]
                assert len(resources.channels) == len(resources.messages) == 1
            finally:
                await runtime.close()

    asyncio.run(scenario())


def test_legacy_install_requires_transactional_cutover_before_domain_enable(legacy):
    async def scenario():
        with pytest.raises(ValueError, match="cutover"):
            await legacy.runtime.enable_skill(guild_id=1, skill_id="event-manager")
        plan = await prepare(legacy.runtime, guild_id=1)
        assert commit(legacy, plan)
        web = GamerHQWebPlatformService(bot=legacy.runtime.bot, skill_runtime=legacy.runtime)
        public_status = await web.get_skill(guild_id=1, skill_id="event-manager", authorized_guild_ids=[1])
        assert public_status["state"]["configured"] is True
        await legacy.runtime.enable_skill(guild_id=1, skill_id="event-manager")
        rows = await legacy.runtime.call_management(guild_id=1, skill_id="event-manager",
            contract_id="event-manager.list.v1", payload={})
        assert rows["events"][0]["legacyEventIds"] == [legacy.event["id"]]
        assert owns_events(1)
    asyncio.run(scenario())


def test_compact_discord_builder_uses_public_skill_persistence_without_duplicate_resources(resources):
    """Real installed Skill + Host SQLite/resources; Discord itself is synthetic."""
    from cogs.event_manager import open_editor
    from cogs import event_manager
    from cogs.event_editor import WeekdayView, EventPreviewView
    from cogs import lfg

    async def scenario():
        guild, member = resources.guild, resources.member
        member.guild_permissions.administrator = False
        member.guild_permissions.manage_guild = False
        bot = SimpleNamespace(guilds=[guild], get_guild=lambda ident: guild if ident == 1 else None)
        db.set_setting("skill_resources:1:event-manager", json.dumps({"categoryId": 10, "allowPublic": False}))
        runtime = GamerHQSkillRuntime(bot)
        bot.skill_runtime = runtime
        load_external_skill_packages(runtime, ("event-manager",))
        await runtime.register_all()
        await runtime.install_skill(guild_id=1, skill_id="event-manager")

        def interaction():
            return SimpleNamespace(client=bot, guild=guild, user=member,
                response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

        async def call(op, payload):
            return await runtime.call_management(guild_id=1, skill_id="event-manager",
                contract_id=f"event-manager.{op}.v1", payload=payload, actor_id=50)

        try:
            create = interaction()
            await open_editor(create)
            builder = create.followup.send.await_args.kwargs["view"]
            assert builder.draft["timezone"] == "Europe/Berlin"
            days = WeekdayView(builder)
            days.selected = {0, 2, 4}
            await days.apply_days.callback(interaction())
            builder.draft.update(name="Compact Raid", game="Arena", description="Practice", voiceEnabled=False)
            preview = interaction()
            await builder.preview.callback(preview)
            view = preview.edit_original_response.await_args.kwargs["view"]
            assert isinstance(view, EventPreviewView) and not view.confirm.disabled
            # No legacy persistence or per-occurrence resource creator may run.
            real_call, interrupted = event_manager.call, False
            async def interrupted_create(interaction, operation, payload):
                nonlocal interrupted
                result = await real_call(interaction, operation, payload)
                if operation == "create" and not interrupted:
                    interrupted = True
                    raise RuntimeError("Synthetic lost create response after commit")
                return result
            with patch.object(lfg, "persist_event_draft", side_effect=AssertionError("legacy write")), \
                    patch.object(lfg, "create_private_event_channel", side_effect=AssertionError("legacy resources")), \
                    patch.object(event_manager, "call", side_effect=interrupted_create):
                await view.confirm.callback(interaction())
                assert not builder.submitted
                await view.confirm.callback(interaction())
                await view.confirm.callback(interaction())
            rows = await call("list", {})
            assert len(rows["events"]) == 1
            event = rows["events"][0]
            assert event["config"]["weekdays"] == [0, 2, 4]
            assert event["config"]["game"] == "Arena"
            assert event["config"]["description"] == "Practice"
            original_revision = event["revision"]

            edit = interaction()
            await open_editor(edit, event["id"])
            editor = edit.followup.send.await_args.kwargs["view"]
            assert editor.revision == original_revision
            editor.draft["name"] = "Edited Compact Raid"
            preview = interaction()
            await editor.preview.callback(preview)
            edit_view = preview.edit_original_response.await_args.kwargs["view"]
            assert edit_view.confirm.label == "Save changes"
            await edit_view.confirm.callback(interaction())
            await call("reconcile", {"eventId": event["id"]})
            current = (await call("get", {"eventId": event["id"]}))["state"]
            assert current["config"]["name"] == "Edited Compact Raid"
            assert current["revision"] > original_revision
            assert current["chat"]["channelId"] == "100"
            assert len(resources.channels) == len(resources.messages) == 1
            assert guild.create_text_channel.await_count == 1
            assert len((await call("history", {"eventId": event["id"]}))["occurrences"]) > 1
            assert owns_events(1)
        finally:
            await runtime.close()

    asyncio.run(scenario())
