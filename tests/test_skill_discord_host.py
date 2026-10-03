import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from hosts.gamerhq.discord_host import GamerHQDiscordAdapter, SkillDiscordError
from hosts.gamerhq.skill_host import CapabilityPermissions
from skill_runtime.contracts.capabilities import SkillCapability


class SkillDiscordAdapterTests(unittest.IsolatedAsyncioTestCase):
    def guild(self):
        bot_member = SimpleNamespace(id=99)
        guild = SimpleNamespace(
            id=1,
            me=bot_member,
            get_member=lambda user_id: SimpleNamespace(id=user_id) if user_id == 10 else None,
            get_role=lambda role_id: SimpleNamespace(id=role_id) if role_id == 20 else None,
        )
        return guild

    def adapter(self, *caps):
        return GamerHQDiscordAdapter(
            guild=self.guild(),
            permissions=CapabilityPermissions(caps),
        )

    def channel(self, *, send=True, embeds=True, mention_everyone=False):
        rights = SimpleNamespace(
            view_channel=True,
            send_messages=send,
            send_messages_in_threads=send,
            embed_links=embeds,
            mention_everyone=mention_everyone,
        )
        message = SimpleNamespace(id=123)
        return SimpleNamespace(
            permissions_for=lambda member: rights,
            send=AsyncMock(return_value=message),
            fetch_message=AsyncMock(),
        )

    async def test_send_requires_capability_and_defaults_to_no_mentions(self):
        adapter = self.adapter(SkillCapability.DISCORD_MESSAGES_SEND.value)
        channel = self.channel()
        with patch.object(adapter, "_channel", return_value=channel):
            message_id = await adapter.send_message(channel_id=1, content="Hello")
        self.assertEqual(message_id, 123)
        mentions = channel.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)

        denied = self.adapter()
        with patch.object(denied, "_channel", return_value=channel):
            with self.assertRaisesRegex(PermissionError, "discord.messages.send"):
                await denied.send_message(channel_id=1, content="Hello")

    async def test_embed_requires_separate_capability_and_effective_permission(self):
        adapter = self.adapter(
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.DISCORD_EMBEDS_SEND.value,
        )
        blocked = self.channel(embeds=False)
        with patch.object(adapter, "_channel", return_value=blocked):
            with self.assertRaisesRegex(SkillDiscordError, "cannot send embeds"):
                await adapter.send_message(
                    channel_id=1,
                    embed={"title": "Demo"},
                )

    async def test_everyone_mentions_require_explicit_capability_and_discord_permission(self):
        base = (SkillCapability.DISCORD_MESSAGES_SEND.value,)
        adapter = self.adapter(*base)
        channel = self.channel(mention_everyone=True)
        with patch.object(adapter, "_channel", return_value=channel):
            with self.assertRaisesRegex(PermissionError, "discord.mentions.everyone"):
                await adapter.send_message(
                    channel_id=1,
                    content="@everyone",
                    allowed_mentions={"everyone": True},
                )

        adapter = self.adapter(
            *base,
            SkillCapability.DISCORD_MENTIONS_EVERYONE.value,
        )
        channel = self.channel(mention_everyone=False)
        with patch.object(adapter, "_channel", return_value=channel):
            with self.assertRaisesRegex(SkillDiscordError, "cannot mention everyone"):
                await adapter.send_message(
                    channel_id=1,
                    content="@everyone",
                    allowed_mentions={"everyone": True},
                )

    async def test_allowed_user_and_role_mentions_must_resolve_inside_guild(self):
        adapter = self.adapter(SkillCapability.DISCORD_MESSAGES_SEND.value)
        channel = self.channel()
        with patch.object(adapter, "_channel", return_value=channel):
            await adapter.send_message(
                channel_id=1,
                content="Hi",
                allowed_mentions={"users": [10], "roles": [20]},
            )
            with self.assertRaisesRegex(SkillDiscordError, "user mention"):
                await adapter.send_message(
                    channel_id=1,
                    content="Hi",
                    allowed_mentions={"users": [999]},
                )

    async def test_edit_and_delete_are_limited_to_gamerhq_authored_messages(self):
        adapter = self.adapter(
            SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value,
            SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value,
        )
        channel = self.channel()
        foreign = SimpleNamespace(
            author=SimpleNamespace(id=777),
            edit=AsyncMock(),
            delete=AsyncMock(),
        )
        channel.fetch_message.return_value = foreign
        with patch.object(adapter, "_channel", return_value=channel):
            with self.assertRaisesRegex(SkillDiscordError, "only GamerHQ-authored"):
                await adapter.edit_own_message(
                    channel_id=1,
                    message_id=2,
                    content="Changed",
                )
            with self.assertRaisesRegex(SkillDiscordError, "only GamerHQ-authored"):
                await adapter.delete_own_message(channel_id=1, message_id=2)

    async def test_message_content_limit_is_checked_before_discord(self):
        adapter = self.adapter(SkillCapability.DISCORD_MESSAGES_SEND.value)
        channel = self.channel()
        with patch.object(adapter, "_channel", return_value=channel):
            with self.assertRaisesRegex(SkillDiscordError, "2000-character"):
                await adapter.send_message(channel_id=1, content="x" * 2001)
        channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
