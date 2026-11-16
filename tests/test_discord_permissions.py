import asyncio
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock

import discord
import pytest

from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from services import discord_permissions as access
from skill_runtime.contracts import DiscordGuildPermission
from skill_runtime.contracts.manifest import validate_manifest
from tests.test_skill_discord_host import FakeSkill


class Member:
    id = 90

    @property
    def guild_permissions(self):
        return self.guild.default_role.permissions | self.guild.get_role(2).permissions


def guild_permissions_fixture(**flags):
    default = SimpleNamespace(id=1, permissions=discord.Permissions.none())
    host = SimpleNamespace(id=2, permissions=discord.Permissions(**flags))
    member = Member()
    guild = SimpleNamespace(id=1, owner_id=11, me=member, fetch_roles=AsyncMock(return_value=[default, host]),
                            fetch_member=AsyncMock(return_value=member))
    return guild, host


def test_core_install_url_only_requests_core_mask():
    query = parse_qs(urlparse(access.core_install_url(123)).query)
    granted = discord.Permissions(int(query['permissions'][0]))
    assert query['integration_type'] == ['0']
    assert set(query['scope'][0].split()) == {'bot', 'applications.commands'}
    assert granted.pin_messages
    assert granted == discord.Permissions(**dict.fromkeys(access.CORE_INSTALL, True))
    assert not (granted.administrator or granted.move_members or granted.manage_events)


def test_old_manifest_and_unsupported_permissions():
    manifest = FakeSkill().manifest
    legacy = SimpleNamespace(**{name: getattr(manifest, name) for name in manifest.__slots__
                               if name != 'discord_guild_permissions'})
    validate_manifest(legacy)
    assert manifest.discord_guild_permissions == ()
    for flags in [('administrator',), ('pin_messages', 'pin_messages'), (42,)]:
        with pytest.raises(ValueError, match='Discord guild permission'):
            replace(manifest, discord_guild_permissions=flags)


def test_activation_rechecks_grants_before_mutation_and_restore():
    asyncio.run(verify_activation_preflight())


async def verify_activation_preflight():
    guild, host = guild_permissions_fixture()
    runtime = GamerHQSkillRuntime(SimpleNamespace(get_guild=lambda _: guild))
    skill = FakeSkill()
    skill.manifest = replace(skill.manifest, discord_guild_permissions=(DiscordGuildPermission.PIN_MESSAGES,))
    runtime.register(skill)
    runtime.manager.enable = AsyncMock(return_value=True)
    runtime.manager.start = AsyncMock(return_value=True)
    runtime.context = AsyncMock()
    with pytest.raises(access.DiscordPermissionPreflightError, match='Pin Messages'):
        await runtime.enable_skill(guild_id=1, skill_id=skill.manifest.id)
    runtime.manager.enable.assert_not_awaited()
    runtime.manager.start.assert_not_awaited()
    runtime.context.assert_not_awaited()

    host.permissions.pin_messages = True
    assert await runtime.enable_skill(guild_id=1, skill_id=skill.manifest.id)
    runtime.manager.enable.assert_awaited_once()
    runtime.manager.start.assert_awaited_once()

    host.permissions.pin_messages = False
    runtime.state.enabled_skill_ids = AsyncMock(return_value=(skill.manifest.id,))
    assert await runtime.restore_guild(guild_id=1) == ()
    runtime.manager.start.assert_awaited_once()
    assert guild.fetch_roles.await_count == 3


def test_permission_remediation_distinguishes_synced_and_independent_overwrites():
    guild, host = guild_permissions_fixture(pin_messages=True)
    default = guild.fetch_roles.return_value[0]
    guild.get_role = {1: default, 2: host}.get
    denied = discord.Permissions.none()
    parent = SimpleNamespace(id=10, overwrites={1: 'deny'}, permissions_for=lambda _: denied)
    guild.get_channel = {parent.id: parent}.get
    synced = SimpleNamespace(category_id=parent.id, overwrites={1: 'deny'})
    independent = SimpleNamespace(category_id=parent.id, overwrites={90: 'deny'})
    assert 'Synced category' in access.channel_remediation(guild, synced, Member(), ('pin_messages',))
    assert 'independent overrides' in access.channel_remediation(guild, independent, Member(), ('pin_messages',))
