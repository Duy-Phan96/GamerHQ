import unittest
from types import SimpleNamespace

import discord

import test_onboarding as fixtures
from database import db
from services import booster_service as boosters


class BoosterServiceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    async def asyncSetUp(self):
        self.guild.owner_id = 42
        self.owner = SimpleNamespace(
            id=42,
            guild_permissions=discord.Permissions(administrator=True),
        )
        self.guild.members = [self.owner]
        self.booster = self.guild.role(700)
        self.booster.name = "Server Booster"
        self.booster.managed = True
        self.booster.members = []
        self.guild.roles.append(self.booster)
        self.guild.premium_subscriber_role = self.booster

    def test_discord_managed_role_is_required_and_never_recreated(self):
        original_roles = list(self.guild.roles)
        self.guild.premium_subscriber_role = None
        state = boosters.inspect(self.guild)
        self.assertEqual(state["status"], "BLOCKED")
        self.assertIn("managed Server Booster role", state["reason"])
        with self.assertRaises(ValueError):
            boosters.preview(self.guild, self.owner)
        self.assertEqual(original_roles, self.guild.roles)

    async def test_reviewed_create_is_private_idempotent_and_pinned(self):
        plan = boosters.preview(self.guild, self.owner)
        self.assertEqual(plan.action, "CREATE")
        before = len(self.guild.text_channels)

        channel = await boosters.apply(self.guild, self.owner, plan)

        self.assertEqual(len(self.guild.text_channels), before + 1)
        self.assertEqual(channel.name, boosters.LABEL)
        self.assertEqual(channel.topic, boosters.TOPIC)
        self.assertEqual(
            db.get_setting(f"managed_channel:{self.guild.id}:{boosters.NAME}"),
            str(channel.id),
        )
        self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
        self.assertTrue(channel.overwrites_for(self.booster).view_channel)
        self.assertTrue(channel.overwrites_for(self.booster).send_messages)
        self.assertTrue(channel.overwrites_for(self.guild.mod).view_channel)
        self.assertTrue(channel.overwrites_for(self.guild.me).view_channel)

        pins = [message for message in channel.messages.values() if message.pinned]
        self.assertEqual(len(pins), 1)
        self.assertIn("Boosting is appreciated", pins[0].content)

        verify = boosters.preview(self.guild, self.owner)
        self.assertEqual(verify.action, "VERIFY")
        same = await boosters.apply(self.guild, self.owner, verify)
        self.assertEqual(same.id, channel.id)
        self.assertEqual(len(self.guild.text_channels), before + 1)
        self.assertEqual(len([m for m in channel.messages.values() if m.pinned]), 1)

    async def test_existing_named_channel_is_adopted_not_duplicated(self):
        community = boosters.community_category(self.guild)
        existing = self.guild.add_channel("booster-lounge", community)
        existing.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=True)
        before = len(self.guild.text_channels)

        plan = boosters.preview(self.guild, self.owner)
        self.assertEqual(plan.action, "ADOPT")
        self.assertEqual(plan.channel_id, existing.id)

        result = await boosters.apply(self.guild, self.owner, plan)
        self.assertEqual(result.id, existing.id)
        self.assertEqual(len(self.guild.text_channels), before)
        self.assertFalse(result.overwrites_for(self.guild.default_role).view_channel)
        self.assertTrue(result.overwrites_for(self.booster).view_channel)

    async def test_changed_booster_role_invalidates_open_review(self):
        plan = boosters.preview(self.guild, self.owner)
        replacement = self.guild.role(701)
        replacement.name = "Replacement Booster"
        replacement.managed = True
        self.guild.roles.append(replacement)
        self.guild.premium_subscriber_role = replacement

        with self.assertRaisesRegex(ValueError, "changed"):
            await boosters.apply(self.guild, self.owner, plan)
        self.assertIsNone(db.get_setting(f"managed_channel:{self.guild.id}:{boosters.NAME}"))

    async def test_review_actor_must_still_be_authorized(self):
        plan = boosters.preview(self.guild, self.owner)
        self.guild.members = []
        with self.assertRaises(PermissionError):
            await boosters.apply(self.guild, self.owner, plan)


if __name__ == "__main__":
    unittest.main()
