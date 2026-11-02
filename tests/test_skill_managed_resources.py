import asyncio
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from database import db
from hosts.gamerhq.skill_host import CapabilityPermissions
from hosts.gamerhq.skill_resources import GamerHQManagedResources
from skill_runtime.contracts.errors import CapabilityUnavailableError, InvalidHostOperationError, TransientHostError


@pytest.fixture
def fixture():
    with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", Path(directory) / "resources.db"):
        db.init_db()
        guild = MagicMock(spec=discord.Guild)
        guild.id = 1
        guild.me = MagicMock(spec=discord.Member)
        guild.me.id = 999
        guild.default_role = MagicMock(spec=discord.Role)
        guild.default_role.id = 1
        parent = MagicMock(spec=discord.CategoryChannel)
        parent.id, parent.guild = 10, guild
        member = MagicMock(spec=discord.Member)
        member.id, member.guild = 50, guild
        guild.get_member.side_effect = lambda ident: member if ident == 50 else None
        channels, messages = [], []
        guild.get_channel.side_effect = lambda ident: next((c for c in [parent, *channels] if c.id == ident), None)
        guild.fetch_channels = AsyncMock(side_effect=lambda: list(channels))
        def make_channel(name, **kwargs):
            channel = MagicMock(spec=discord.TextChannel)
            channel.id, channel.name, channel.guild, channel.category_id, channel.topic = 100, name, guild, parent.id, kwargs["topic"]
            overrides = dict(kwargs["overwrites"])
            channel.overwrites_for.side_effect = lambda target: copy.copy(overrides.get(target, discord.PermissionOverwrite()))
            async def set_permissions(target, *, overwrite, **kwargs):
                overrides[target] = copy.copy(overwrite)
            channel.set_permissions = AsyncMock(side_effect=set_permissions)
            channel.edit = AsyncMock()
            async def send(**kwargs):
                message = SimpleNamespace(id=200, author=guild.me, embeds=[kwargs["embed"]], edit=AsyncMock())
                messages.append(message)
                return message
            channel.send = AsyncMock(side_effect=send)
            channel.fetch_message = AsyncMock(side_effect=lambda ident: next(m for m in messages if m.id == ident))
            async def history(**kwargs):
                for message in messages:
                    yield message
            channel.history.side_effect = history
            channel.test_overrides = overrides
            channels.append(channel)
            return channel
        guild.create_text_channel = AsyncMock(side_effect=make_channel)
        policy = {"categoryId": 10, "allowPublic": False, "maxResources": 10}
        adapter = lambda **kw: GamerHQManagedResources(guild=guild, skill_id=kw.get("skill_id", "event-manager"),
            permissions=CapabilityPermissions(("discord.resources.manage",)), policy=kw.get("policy", policy))
        yield SimpleNamespace(guild=guild, member=member, channels=channels, messages=messages, adapter=adapter)


def spec(**changes):
    return {"key": "event:raid", "name": "Raid", "visibility": "private", "member_ids": (50,), "chat_enabled": True,
            "overview": "Next raid at 19:00", **changes}


def test_restart_and_retries_keep_one_channel_one_card_and_revoke_member_access(fixture):
    async def scenario():
        first = await fixture.adapter().ensure_area(**spec())
        assert await fixture.adapter().ensure_area(**spec(overview="Updated raid")) == first
        channel = fixture.channels[0]
        assert channel.test_overrides[fixture.member].view_channel is True
        assert channel.test_overrides[fixture.guild.default_role].view_channel is False
        await fixture.adapter().ensure_area(**spec(member_ids=()))
        assert channel.test_overrides[fixture.member].view_channel is None
        assert fixture.guild.create_text_channel.await_count == 1
        assert channel.send.await_count == 1
        assert len(fixture.messages) == 1
    asyncio.run(scenario())


def test_uncertain_channel_creation_recovers_by_persisted_marker_without_duplication(fixture):
    async def scenario():
        create = fixture.guild.create_text_channel.side_effect
        async def uncertain(*args, **kwargs):
            create(*args, **kwargs)
            raise TimeoutError("synthetic remote timeout")
        fixture.guild.create_text_channel.side_effect = uncertain
        with pytest.raises(TimeoutError):
            await fixture.adapter().ensure_area(**spec())
        fixture.guild.create_text_channel.side_effect = create
        await fixture.adapter().ensure_area(**spec())
        assert fixture.guild.create_text_channel.await_count == 1
        assert len(fixture.channels) == 1
    asyncio.run(scenario())


def test_uncertain_creation_with_no_owned_match_fails_closed(fixture):
    async def scenario():
        fixture.guild.create_text_channel.side_effect = TimeoutError("synthetic")
        with pytest.raises(TimeoutError):
            await fixture.adapter().ensure_area(**spec())
        with pytest.raises(TransientHostError):
            await fixture.adapter().ensure_area(**spec())
        assert fixture.guild.create_text_channel.await_count == 1
    asyncio.run(scenario())


def test_uncertain_publication_recovers_owned_message_without_duplicate(fixture):
    async def scenario():
        await fixture.adapter().ensure_area(**spec())
        channel = fixture.channels[0]
        with db.connect() as conn:
            import json
            row = conn.execute("SELECT state_json FROM skill_managed_resources").fetchone()
            state = json.loads(row[0])
            state.pop("messageId")
            state["messagePending"] = True
            conn.execute("UPDATE skill_managed_resources SET state_json=?", (json.dumps(state),))
        await fixture.adapter().ensure_area(**spec())
        assert channel.send.await_count == 1
    asyncio.run(scenario())


def test_policy_membership_and_ambiguity_fail_without_publication(fixture):
    async def scenario():
        with pytest.raises(CapabilityUnavailableError):
            await fixture.adapter(policy={}).ensure_area(**spec())
        with pytest.raises(InvalidHostOperationError):
            await fixture.adapter().ensure_area(**spec(visibility="public"))
        await fixture.adapter().ensure_area(**spec())
        clone = MagicMock(spec=discord.TextChannel)
        clone.id, clone.topic = 101, fixture.channels[0].topic
        fixture.channels.append(clone)
        with pytest.raises(InvalidHostOperationError):
            await fixture.adapter().ensure_area(**spec())
        assert fixture.channels[0].send.await_count == 1
    asyncio.run(scenario())


def test_uncached_or_departed_members_are_revoked_without_forgetting_their_overrides(fixture):
    async def scenario():
        await fixture.adapter().ensure_area(**spec())
        fixture.guild.get_member.side_effect = lambda ident: None
        adapter = fixture.adapter()
        with pytest.raises(TransientHostError, match="retained"):
            await adapter.ensure_area(**spec(member_ids=()))
        assert (await adapter.health())["state"] == "DEGRADED"
        adapter.user_lookup = AsyncMock(return_value=fixture.member)
        await adapter.ensure_area(**spec(member_ids=()))
        assert fixture.channels[0].test_overrides[fixture.member].view_channel is None
        adapter.user_lookup.assert_awaited_once_with(50)
        assert (await adapter.health())["state"] == "PASS"
    asyncio.run(scenario())


def test_reusing_retired_resource_keys_cannot_bypass_the_active_quota(fixture):
    adapter = fixture.adapter(policy={"categoryId": 10, "maxResources": 1})
    with adapter._claim("retired", "voice") as (state, save):
        state["reserved"] = False
        save()
    with adapter._claim("active", "text"):
        with pytest.raises(InvalidHostOperationError, match="quota"):
            with adapter._claim("retired", "voice"):
                pytest.fail("A retired key bypassed the resource quota")
        # Idempotent retirement does not reserve a new active resource.
        with adapter._claim("retired", "voice", reserve=False):
            pass


def test_occupied_voice_is_retained_and_empty_owned_voice_is_retired(fixture):
    async def scenario():
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id, voice.guild, voice.category_id, voice.members = 300, fixture.guild, 10, [fixture.member]
        fixture.channels.append(voice)
        adapter = fixture.adapter()
        with adapter._claim("voice:session", "voice") as (state, save):
            state["channelId"] = voice.id
            save()
        assert await adapter.retire_voice(key="voice:session") is False
        voice.delete.assert_not_awaited()
        voice.members = []
        assert await adapter.retire_voice(key="voice:session") is True
        voice.delete.assert_awaited_once()
        assert (await adapter.health())["state"] == "PASS"
    asyncio.run(scenario())


def test_uncertain_voice_retirement_recovers_without_deleting_another_channel(fixture):
    async def scenario():
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id, voice.guild, voice.category_id, voice.members = 300, fixture.guild, 10, []
        fixture.channels.append(voice)
        adapter = fixture.adapter()
        with adapter._claim("voice:session", "voice") as (state, save):
            state["channelId"] = voice.id
            save()
        async def uncertain(**kwargs):
            fixture.channels.remove(voice)
            raise TimeoutError("Synthetic response loss after remote deletion")
        voice.delete.side_effect = uncertain
        with pytest.raises(TimeoutError):
            await adapter.retire_voice(key="voice:session")
        assert (await adapter.health())["state"] == "DEGRADED"
        assert await adapter.retire_voice(key="voice:session") is True
        voice.delete.assert_awaited_once()
        assert (await adapter.health())["state"] == "PASS"
    asyncio.run(scenario())


def test_adopted_legacy_voice_uses_its_original_category_and_is_never_automatically_deleted(fixture):
    async def scenario():
        parent = MagicMock(spec=discord.CategoryChannel)
        parent.id, parent.guild = 20, fixture.guild
        original = fixture.guild.get_channel.side_effect
        fixture.guild.get_channel.side_effect = lambda ident: parent if ident == 20 else original(ident)
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id, voice.guild, voice.category_id, voice.members = 300, fixture.guild, 20, []
        voice.overwrites_for.side_effect = lambda target: discord.PermissionOverwrite(view_channel=False, connect=False)
        fixture.channels.append(voice)
        adapter = fixture.adapter(policy={"categoryId": 10, "voiceCategoryId": 20})
        with adapter._claim("voice:legacy", "voice") as (state, save):
            state.update(channelId=300, retainLegacy=True)
            save()
        result = await adapter.ensure_voice(key="voice:legacy", name="Legacy Raid", member_ids=(50,))
        assert result["channelId"] == "300"
        fixture.guild.create_voice_channel.assert_not_awaited()
        assert await adapter.retire_voice(key="voice:legacy") is False
        voice.delete.assert_not_awaited()
        assert (await adapter.health())["state"] == "PASS"
    asyncio.run(scenario())
