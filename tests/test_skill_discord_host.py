import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord
import test_onboarding as fixtures

from database import db
from hosts.gamerhq.skill_discord import GamerHQDiscordPort, SkillDiscordError
from hosts.gamerhq.skill_host import CapabilityPermissions
from skill_runtime.contracts.capabilities import SkillCapability


class SkillTextChannel(fixtures.FakeChannel):
    __class__ = discord.TextChannel

    def __init__(self, guild, name, category, cid):
        super().__init__(guild, name, category, cid)
        self.last_send = None

    async def send(self, *, content, **kwargs):
        self.last_send = dict(content=content, **kwargs)
        return await super().send(content=content, **kwargs)


class SkillDiscordHostTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    def channel(self):
        self.guild.sequence += 1
        channel = SkillTextChannel(
            self.guild,
            "skill-target",
            self.community,
            self.guild.sequence,
        )
        self.guild.text_channels.append(channel)
        return channel

    def port(self, skill_id="recurring-posts", *caps):
        return GamerHQDiscordPort(
            guild=self.guild,
            skill_id=skill_id,
            permissions=CapabilityPermissions(caps),
        )

    def ownership(self, message_id):
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM skill_discord_messages WHERE guild_id=? AND message_id=?",
                (self.guild.id, message_id),
            ).fetchone()
            return dict(row) if row else None

    async def test_send_records_message_ownership_without_content(self):
        channel = self.channel()
        port = self.port("recurring-posts", SkillCapability.DISCORD_MESSAGES_SEND.value)
        message_id = await port.send_message(channel_id=channel.id, content="Hello")
        row = self.ownership(message_id)
        self.assertEqual(row["skill_id"], "recurring-posts")
        self.assertEqual(row["channel_id"], channel.id)
        self.assertNotIn("content", row)

    async def test_other_skill_cannot_edit_or_delete_owned_message(self):
        channel = self.channel()
        owner = self.port(
            "recurring-posts",
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
            SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value,
        )
        message_id = await owner.send_message(channel_id=channel.id, content="Owned")
        other = self.port(
            "analytics",
            SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
            SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value,
        )
        with self.assertRaisesRegex(PermissionError, "does not own"):
            await other.edit_own_message(
                channel_id=channel.id,
                message_id=message_id,
                content="Changed",
            )
        with self.assertRaisesRegex(PermissionError, "does not own"):
            await other.delete_own_message(
                channel_id=channel.id,
                message_id=message_id,
            )
        self.assertEqual(channel.messages[message_id].content, "Owned")

    async def test_owner_can_edit_and_delete_its_message(self):
        channel = self.channel()
        port = self.port(
            "recurring-posts",
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
            SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value,
        )
        message_id = await port.send_message(channel_id=channel.id, content="Before")
        await port.edit_own_message(
            channel_id=channel.id,
            message_id=message_id,
            content="After",
        )
        self.assertEqual(channel.messages[message_id].content, "After")
        await port.delete_own_message(channel_id=channel.id, message_id=message_id)
        self.assertNotIn(message_id, channel.messages)
        self.assertIsNone(self.ownership(message_id))

    async def test_manually_deleted_message_is_forgotten_gracefully(self):
        channel = self.channel()
        port = self.port(
            "recurring-posts",
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
            SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value,
        )
        message_id = await port.send_message(channel_id=channel.id, content="Before")
        await channel.messages[message_id].delete()
        await port.delete_own_message(channel_id=channel.id, message_id=message_id)
        self.assertIsNone(self.ownership(message_id))

        second = await port.send_message(channel_id=channel.id, content="Again")
        await channel.messages[second].delete()
        with self.assertRaisesRegex(SkillDiscordError, "no longer exists"):
            await port.edit_own_message(
                channel_id=channel.id,
                message_id=second,
                content="Changed",
            )
        self.assertIsNone(self.ownership(second))

    async def test_send_requires_declared_capability(self):
        channel = self.channel()
        port = self.port("recurring-posts")
        with self.assertRaisesRegex(PermissionError, "discord.messages.send"):
            await port.send_message(channel_id=channel.id, content="No")

    async def test_embed_requires_separate_capability_and_safe_urls(self):
        channel = self.channel()
        without = self.port("recurring-posts", SkillCapability.DISCORD_MESSAGES_SEND.value)
        with self.assertRaisesRegex(PermissionError, "discord.embeds.send"):
            await without.send_message(
                channel_id=channel.id,
                embed={"title": "Demo"},
            )

        port = self.port(
            "recurring-posts",
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_EMBEDS_SEND.value,
        )
        message_id = await port.send_message(
            channel_id=channel.id,
            embed={
                "title": "Demo",
                "description": "Safe embed",
                "url": "https://example.com/demo",
                "footer": {"text": "GamerHQ"},
            },
        )
        self.assertIsNotNone(self.ownership(message_id))
        with self.assertRaisesRegex(ValueError, "safe HTTPS URL"):
            await port.send_message(
                channel_id=channel.id,
                embed={"title": "Bad", "url": "http://example.com"},
            )

    async def test_mentions_default_to_none_and_everyone_needs_capability(self):
        channel = self.channel()
        port = self.port("recurring-posts", SkillCapability.DISCORD_MESSAGES_SEND.value)
        await port.send_message(channel_id=channel.id, content="@everyone hello")
        mentions = channel.last_send["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)

        with self.assertRaisesRegex(PermissionError, "discord.mentions.everyone"):
            await port.send_message(
                channel_id=channel.id,
                content="@everyone hello",
                allowed_mentions={"everyone": True},
            )

        allowed = self.port(
            "recurring-posts",
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_MENTIONS_EVERYONE.value,
        )
        await allowed.send_message(
            channel_id=channel.id,
            content="@everyone hello",
            allowed_mentions={"everyone": True},
        )
        self.assertTrue(channel.last_send["allowed_mentions"].everyone)

    async def test_user_and_role_mentions_are_explicitly_allowlisted(self):
        channel = self.channel()
        member = SimpleNamespace(id=1234)
        self.guild.members = [member]
        role = self.guild.custom
        port = self.port("recurring-posts", SkillCapability.DISCORD_MESSAGES_SEND.value)
        await port.send_message(
            channel_id=channel.id,
            content="<@1234> <@&3>",
            allowed_mentions={"users": [member.id], "roles": [role.id]},
        )
        mentions = channel.last_send["allowed_mentions"]
        self.assertEqual([item.id for item in mentions.users], [member.id])
        self.assertEqual([item.id for item in mentions.roles], [role.id])

    async def test_message_content_and_embed_limits_fail_before_send(self):
        channel = self.channel()
        port = self.port(
            "recurring-posts",
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_EMBEDS_SEND.value,
        )
        with self.assertRaisesRegex(ValueError, "2000"):
            await port.send_message(channel_id=channel.id, content="x" * 2001)
        with self.assertRaisesRegex(ValueError, "256"):
            await port.send_message(
                channel_id=channel.id,
                embed={"title": "x" * 257},
            )
        self.assertEqual(channel.sends, 0)

    async def test_failed_ownership_persistence_removes_just_sent_message(self):
        channel = self.channel()
        port = self.port("recurring-posts", SkillCapability.DISCORD_MESSAGES_SEND.value)
        with patch.object(port, "_remember", side_effect=sqlite3.OperationalError("fixture")):
            with self.assertRaisesRegex(SkillDiscordError, "safely recorded"):
                await port.send_message(channel_id=channel.id, content="Transient")
        self.assertEqual(channel.messages, {})

    async def test_invalid_bot_author_invalidates_mapping(self):
        channel = self.channel()
        port = self.port(
            "recurring-posts",
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
        )
        message_id = await port.send_message(channel_id=channel.id, content="Owned")
        channel.messages[message_id].author.id = 77
        with self.assertRaisesRegex(PermissionError, "no longer owned"):
            await port.edit_own_message(
                channel_id=channel.id,
                message_id=message_id,
                content="No",
            )
        self.assertIsNone(self.ownership(message_id))


if __name__ == "__main__":
    unittest.main()
