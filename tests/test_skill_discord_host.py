import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from hosts.gamerhq.skill_discord import GamerHQDiscordAdapter
from hosts.gamerhq.skill_host import CapabilityPermissions
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime, HOST_CAPABILITIES
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.errors import (
    CapabilityUnavailableError,
    HostPermissionDeniedError,
    ResourceNotFoundError,
)
from skill_runtime.contracts.lifecycle import SkillHealth
from skill_runtime.contracts.manifest import SkillManifest


class FakeGuild:
    def __init__(self, guild_id=1):
        self.id = guild_id
        self.channels = {}
        self.members = {}
        self.roles = {}
        top_role = MagicMock(spec=discord.Role)
        top_role.id = 999
        self.me = SimpleNamespace(
            top_role=top_role,
            guild_permissions=discord.Permissions(manage_roles=True),
        )

    def get_channel_or_thread(self, channel_id):
        return self.channels.get(channel_id)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_member(self, member_id):
        return self.members.get(member_id)

    def get_role(self, role_id):
        return self.roles.get(role_id)

    async def fetch_member(self, member_id):
        member = self.members.get(member_id)
        if member is None:
            response = SimpleNamespace(status=404, reason="Not Found")
            raise discord.NotFound(response, "missing")
        return member


class FakeChannel:
    def __init__(self, guild, channel_id=10, *, name="general"):
        self.guild = guild
        self.id = channel_id
        self.name = name
        self.send = AsyncMock(return_value=SimpleNamespace(id=9001))


class FakeSkill:
    def __init__(self, permissions=()):
        self.manifest = SkillManifest(
            id="fixture-skill",
            name="Fixture Skill",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=tuple(permissions),
        )
        self.calls = []

    async def register(self, ctx):
        self.calls.append("register")

    async def enable(self, ctx):
        self.calls.append("enable")

    async def disable(self, ctx):
        self.calls.append("disable")

    async def start(self, ctx):
        self.calls.append("start")

    async def stop(self, ctx):
        self.calls.append("stop")

    async def health_check(self, ctx):
        return SkillHealth("PASS", "ok")


class DiscordAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guild = FakeGuild()
        self.channel = FakeChannel(self.guild)
        self.guild.channels[self.channel.id] = self.channel

    def adapter(self, *declared):
        return GamerHQDiscordAdapter(
            guild=self.guild,
            skill_id="fixture-skill",
            permissions=CapabilityPermissions(declared, available=HOST_CAPABILITIES),
        )

    async def test_member_lookup_is_guild_scoped_and_host_neutral(self):
        member_role = SimpleNamespace(id=44, is_default=lambda: False)
        everyone = SimpleNamespace(id=1, is_default=lambda: True)
        member = SimpleNamespace(
            id=7,
            guild=self.guild,
            display_name="Player",
            name="player",
            roles=[everyone, member_role],
            joined_at=None,
            bot=False,
        )
        self.guild.members[member.id] = member
        adapter = self.adapter(SkillCapability.DISCORD_MEMBERS_READ.value)

        info = await adapter.get_member(member_id=member.id)

        self.assertEqual(info.id, 7)
        self.assertEqual(info.display_name, "Player")
        self.assertEqual(info.role_ids, (44,))
        self.assertFalse(info.is_bot)

        other_guild = FakeGuild(2)
        self.guild.members[8] = SimpleNamespace(
            id=8,
            guild=other_guild,
            display_name="Cross",
            roles=[],
            joined_at=None,
            bot=False,
        )
        with self.assertRaises(ResourceNotFoundError):
            await adapter.get_member(member_id=8)

    async def test_member_read_requires_explicit_capability(self):
        adapter = self.adapter()
        with self.assertRaisesRegex(PermissionError, "discord.members.read"):
            await adapter.get_member(member_id=7)

    async def test_reward_role_grant_requires_safe_permissionless_role(self):
        role = MagicMock(spec=discord.Role)
        role.id = 44
        role.managed = False
        role.permissions = discord.Permissions.none()
        role.is_default.return_value = False
        role.__lt__.return_value = True
        member = SimpleNamespace(
            id=7,
            guild=self.guild,
            roles=[],
            add_roles=AsyncMock(),
            remove_roles=AsyncMock(),
        )
        self.guild.roles[44] = role
        self.guild.members[7] = member

        adapter = self.adapter(SkillCapability.DISCORD_ROLES_MANAGE.value)
        self.assertTrue(await adapter.grant_role(member_id=7, role_id=44))
        member.add_roles.assert_awaited_once_with(role, reason="Skill reward: fixture-skill")

    async def test_reward_role_grant_refuses_permission_bearing_role(self):
        role = MagicMock(spec=discord.Role)
        role.id = 45
        role.managed = False
        role.permissions = discord.Permissions(administrator=True)
        role.is_default.return_value = False
        role.__lt__.return_value = True
        self.guild.roles[45] = role
        self.guild.members[7] = SimpleNamespace(id=7, guild=self.guild, roles=[], add_roles=AsyncMock())

        adapter = self.adapter(SkillCapability.DISCORD_ROLES_MANAGE.value)
        with self.assertRaisesRegex(Exception, "Permission-bearing"):
            await adapter.grant_role(member_id=7, role_id=45)

    async def test_preexisting_reward_role_is_not_regranted(self):
        role = MagicMock(spec=discord.Role)
        role.id = 46
        role.managed = False
        role.permissions = discord.Permissions.none()
        role.is_default.return_value = False
        role.__lt__.return_value = True
        member = SimpleNamespace(
            id=7,
            guild=self.guild,
            roles=[role],
            add_roles=AsyncMock(),
            remove_roles=AsyncMock(),
        )
        self.guild.roles[46] = role
        self.guild.members[7] = member

        adapter = self.adapter(SkillCapability.DISCORD_ROLES_MANAGE.value)
        self.assertFalse(await adapter.grant_role(member_id=7, role_id=46))
        member.add_roles.assert_not_awaited()

    async def test_channel_lookup_is_guild_scoped_and_host_neutral(self):
        adapter = self.adapter(SkillCapability.DISCORD_CHANNELS_READ.value)
        info = await adapter.get_channel(channel_id=self.channel.id)
        self.assertEqual((info.id, info.name), (10, "general"))

        other_guild = FakeGuild(2)
        cross = FakeChannel(other_guild, 11)
        self.guild.channels[cross.id] = cross
        with self.assertRaises(ResourceNotFoundError):
            await adapter.get_channel(channel_id=cross.id)

    async def test_undeclared_discord_capability_fails_closed(self):
        adapter = self.adapter()
        with self.assertRaisesRegex(PermissionError, "did not declare"):
            await adapter.send_message(channel_id=self.channel.id, content="hello")
        self.channel.send.assert_not_awaited()

    async def test_send_uses_only_scoped_channel_and_returns_message_id(self):
        adapter = self.adapter(SkillCapability.DISCORD_MESSAGES_SEND.value)
        message_id = await adapter.send_message(channel_id=self.channel.id, content="hello")
        self.assertEqual(message_id, 9001)
        self.channel.send.assert_awaited_once()
        kwargs = self.channel.send.await_args.kwargs
        self.assertEqual(kwargs["content"], "hello")
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)

    async def test_send_renders_safe_link_buttons(self):
        adapter = self.adapter(SkillCapability.DISCORD_MESSAGES_SEND.value)
        await adapter.send_message(
            channel_id=self.channel.id,
            content="offer",
            link_buttons=({"label": "View offer", "url": "https://example.com/deal"},),
        )

        view = self.channel.send.await_args.kwargs["view"]
        self.assertIsInstance(view, discord.ui.View)
        self.assertEqual(len(view.children), 1)
        button = view.children[0]
        self.assertEqual(button.label, "View offer")
        self.assertEqual(button.url, "https://example.com/deal")
        self.assertEqual(button.style, discord.ButtonStyle.link)

    async def test_link_buttons_reject_unsafe_urls_and_too_many_buttons(self):
        adapter = self.adapter(SkillCapability.DISCORD_MESSAGES_SEND.value)
        with self.assertRaisesRegex(Exception, "must use HTTPS"):
            await adapter.send_message(
                channel_id=self.channel.id,
                content="offer",
                link_buttons=({"label": "Bad", "url": "javascript:alert(1)"},),
            )
        with self.assertRaisesRegex(Exception, "at most 5"):
            await adapter.send_message(
                channel_id=self.channel.id,
                content="offer",
                link_buttons=tuple(
                    {"label": f"Link {index}", "url": "https://example.com"}
                    for index in range(6)
                ),
            )

    async def test_embed_requires_separate_embed_capability(self):
        adapter = self.adapter(SkillCapability.DISCORD_MESSAGES_SEND.value)
        with self.assertRaisesRegex(PermissionError, "discord.embeds.send"):
            await adapter.send_message(
                channel_id=self.channel.id,
                embed={"title": "Fixture"},
            )

    async def test_discord_forbidden_is_translated_without_private_text(self):
        response = SimpleNamespace(status=403, reason="Forbidden")
        self.channel.send.side_effect = discord.Forbidden(response, "private Discord detail")
        adapter = self.adapter(SkillCapability.DISCORD_MESSAGES_SEND.value)
        with self.assertRaises(HostPermissionDeniedError) as caught:
            await adapter.send_message(channel_id=self.channel.id, content="hello")
        self.assertNotIn("private Discord detail", str(caught.exception))

    async def test_edit_delete_remain_unavailable_until_ownership_is_defined(self):
        adapter = self.adapter(
            SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
            SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value,
        )
        with self.assertRaises(CapabilityUnavailableError):
            await adapter.edit_own_message(channel_id=10, message_id=20, content="x")
        with self.assertRaises(CapabilityUnavailableError):
            await adapter.delete_own_message(channel_id=10, message_id=20)


class GamerHQSkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_activation_rejects_required_capability_host_does_not_offer(self):
        guild = FakeGuild()
        bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == guild.id else None)
        runtime = GamerHQSkillRuntime(bot)
        skill = FakeSkill((SkillCapability.DISCORD_VOICE_MANAGE.value,))
        runtime.register(skill)

        with self.assertRaisesRegex(CapabilityUnavailableError, "discord.voice.manage"):
            await runtime.enable_skill(guild_id=1, skill_id="fixture-skill")
        self.assertEqual(skill.calls, [])

    async def test_context_contains_scoped_discord_adapter_not_discord_client(self):
        guild = FakeGuild()
        bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == guild.id else None)
        runtime = GamerHQSkillRuntime(bot)
        skill = FakeSkill((
            SkillCapability.DISCORD_CHANNELS_READ.value,
            SkillCapability.DISCORD_MESSAGES_SEND.value,
        ))
        runtime.register(skill)
        ctx = await runtime.context(1, "fixture-skill")
        self.assertEqual(ctx.guild_id, 1)
        self.assertIsInstance(ctx.discord, GamerHQDiscordAdapter)
        self.assertFalse(hasattr(ctx.discord, "client"))
        self.assertFalse(hasattr(ctx.discord, "bot"))


if __name__ == "__main__":
    unittest.main()
