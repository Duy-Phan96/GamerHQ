import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from hosts.gamerhq.skill_discord import GamerHQDiscordPort
from hosts.gamerhq.skill_host import CapabilityPermissions
from skill_runtime.contracts.capabilities import SkillCapability


class Policy:
    def __init__(self, allowed=()):
        self.allowed=set(allowed)
        self.calls=[]

    def allows(self, *, guild_id, skill_id, channel_id, operation):
        self.calls.append((guild_id,skill_id,channel_id,operation))
        return (channel_id,operation) in self.allowed


class SkillDiscordHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.me=SimpleNamespace(id=900)
        self.guild=SimpleNamespace(id=1,me=self.me)
        self.channel=SimpleNamespace(id=10,guild=self.guild)
        self.guild.get_channel=lambda cid: self.channel if cid==10 else None
        self.message=SimpleNamespace(
            id=100,
            author=self.me,
            edit=AsyncMock(),
            delete=AsyncMock(),
        )
        self.channel.send=AsyncMock(return_value=self.message)
        self.channel.fetch_message=AsyncMock(return_value=self.message)

    def permissions(self,*values):
        return CapabilityPermissions(values)

    def port(self,*capabilities,allowed=()):
        return GamerHQDiscordPort(
            guild=self.guild,
            skill_id="fixture-skill",
            permissions=self.permissions(*capabilities),
            policy=Policy(allowed),
        )

    async def test_default_resource_policy_denies_discord_access(self):
        port=GamerHQDiscordPort(
            guild=self.guild,
            skill_id="fixture-skill",
            permissions=self.permissions(SkillCapability.DISCORD_MESSAGES_SEND.value),
        )
        with self.assertRaisesRegex(PermissionError,"not allowed"):
            await port.send_message(channel_id=10,content="hello")
        self.channel.send.assert_not_awaited()

    async def test_send_requires_capability_and_resource_scope(self):
        port=self.port(allowed={(10,"messages.send")})
        with self.assertRaisesRegex(PermissionError,"did not declare"):
            await port.send_message(channel_id=10,content="hello")

        port=self.port(
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            allowed={(10,"messages.send")},
        )
        message_id=await port.send_message(channel_id=10,content="hello")
        self.assertEqual(message_id,100)
        self.channel.send.assert_awaited_once()
        self.assertFalse(self.channel.send.await_args.kwargs["allowed_mentions"].everyone)

    async def test_embed_requires_separate_capability(self):
        port=self.port(
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            allowed={(10,"messages.send")},
        )
        with self.assertRaisesRegex(PermissionError,"discord.embeds.send"):
            await port.send_message(channel_id=10,embed={"title":"Hello"})

        port=self.port(
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_EMBEDS_SEND.value,
            allowed={(10,"messages.send")},
        )
        await port.send_message(channel_id=10,embed={"title":"Hello"})
        sent=self.channel.send.await_args.kwargs["embed"]
        self.assertIsInstance(sent,discord.Embed)
        self.assertEqual(sent.title,"Hello")

    async def test_role_and_everyone_mentions_need_explicit_capabilities_and_scope(self):
        base=(SkillCapability.DISCORD_MESSAGES_SEND.value,)
        allowed={(10,"messages.send"),(10,"mentions.roles"),(10,"mentions.everyone")}

        with self.assertRaisesRegex(PermissionError,"discord.mentions.roles"):
            await self.port(*base,allowed=allowed).send_message(
                channel_id=10,content="<@&123>",allowed_mentions={"roles":True}
            )

        role_port=self.port(
            *base,SkillCapability.DISCORD_MENTIONS_ROLES.value,allowed=allowed
        )
        await role_port.send_message(
            channel_id=10,content="<@&123>",allowed_mentions={"roles":True}
        )
        mentions=self.channel.send.await_args.kwargs["allowed_mentions"]
        self.assertTrue(mentions.roles)
        self.assertFalse(mentions.everyone)

        everyone_port=self.port(
            *base,SkillCapability.DISCORD_MENTIONS_EVERYONE.value,allowed=allowed
        )
        await everyone_port.send_message(
            channel_id=10,content="@everyone",allowed_mentions={"everyone":True}
        )
        self.assertTrue(self.channel.send.await_args.kwargs["allowed_mentions"].everyone)

    async def test_edit_and_delete_only_bot_owned_messages(self):
        allowed={(10,"messages.edit_own"),(10,"messages.delete_own")}
        port=self.port(
            SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
            SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value,
            allowed=allowed,
        )
        await port.edit_own_message(channel_id=10,message_id=100,content="updated")
        await port.delete_own_message(channel_id=10,message_id=100)
        self.message.edit.assert_awaited_once()
        self.message.delete.assert_awaited_once()

        other=SimpleNamespace(id=101,author=SimpleNamespace(id=777),edit=AsyncMock(),delete=AsyncMock())
        self.channel.fetch_message=AsyncMock(return_value=other)
        with self.assertRaisesRegex(PermissionError,"only messages owned"):
            await port.edit_own_message(channel_id=10,message_id=101,content="x")
        with self.assertRaisesRegex(PermissionError,"only messages owned"):
            await port.delete_own_message(channel_id=10,message_id=101)
        other.edit.assert_not_awaited()
        other.delete.assert_not_awaited()

    async def test_invalid_or_cross_guild_channel_fails_before_send(self):
        port=self.port(
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            allowed={(10,"messages.send")},
        )
        with self.assertRaisesRegex(ValueError,"not available"):
            await port.send_message(channel_id=999,content="hello")
        foreign=SimpleNamespace(id=10,guild=SimpleNamespace(id=2),send=AsyncMock())
        self.guild.get_channel=lambda cid: foreign
        with self.assertRaisesRegex(ValueError,"not available"):
            await port.send_message(channel_id=10,content="hello")


if __name__=="__main__":
    unittest.main()
