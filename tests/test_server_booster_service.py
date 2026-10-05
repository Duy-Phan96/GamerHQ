import unittest

import discord
import test_onboarding as fixtures

from database import db
from services import server_booster_service as boosters


class ServerBoosterServiceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    async def asyncSetUp(self):
        self.booster = self.guild.role(5000)
        self.booster.name = "Server Booster"
        self.booster.managed = True
        self.guild.roles.append(self.booster)
        self.guild.premium_subscriber_role = self.booster
        db.set_setting(f"managed_category:{self.guild.id}:community", self.community.id)

    def test_missing_native_booster_role_fails_without_replacement(self):
        self.guild.premium_subscriber_role = None
        before = list(self.guild.roles)
        with self.assertRaisesRegex(ValueError, "will not create a replacement"):
            boosters.status(self.guild)
        self.assertEqual(before, self.guild.roles)

    async def test_confirmed_setup_creates_private_lounge_and_persists_mapping(self):
        preview = boosters.review_snapshot(self.guild)
        self.assertEqual(preview["action"], "create")

        channel, changed = await boosters.apply(self.guild, preview)

        self.assertTrue(changed)
        self.assertEqual(channel.name, boosters.CHANNEL_NAME)
        self.assertEqual(channel.category_id, self.community.id)
        self.assertEqual(
            db.get_setting(f"managed_channel:{self.guild.id}:booster-lounge"),
            str(channel.id),
        )
        self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
        self.assertTrue(channel.overwrites_for(self.booster).view_channel)
        self.assertTrue(channel.overwrites_for(self.booster).send_messages)
        self.assertTrue(channel.overwrites_for(self.guild.mod).view_channel)
        self.assertTrue(channel.overwrites_for(self.guild.me).manage_channels)

        status = boosters.status(self.guild)
        self.assertEqual(status["action"], "ready")
        self.assertIs(status["channel"], channel)

    async def test_repair_removes_unknown_grant_and_restores_policy(self):
        channel = self.guild.add_channel("💎・booster-lounge", self.community)
        channel.topic = "Wrong"
        channel.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True)
        db.set_setting(f"managed_channel:{self.guild.id}:booster-lounge", channel.id)

        preview = boosters.review_snapshot(self.guild)
        self.assertEqual(preview["action"], "repair")
        repaired, changed = await boosters.apply(self.guild, preview)

        self.assertTrue(changed)
        self.assertIs(repaired, channel)
        self.assertEqual(channel.topic, boosters.CHANNEL_TOPIC)
        self.assertNotIn(self.guild.custom, channel.overwrites)
        self.assertTrue(channel.overwrites_for(self.booster).view_channel)
        self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)

    async def test_existing_unmapped_lounge_is_adopted_only_after_confirmation(self):
        channel = self.guild.add_channel("booster-lounge", self.community)
        self.assertIsNone(db.get_setting(f"managed_channel:{self.guild.id}:booster-lounge"))

        preview = boosters.review_snapshot(self.guild)
        self.assertEqual(preview["action"], "adopt")
        self.assertIsNone(db.get_setting(f"managed_channel:{self.guild.id}:booster-lounge"))

        adopted, changed = await boosters.apply(self.guild, preview)
        self.assertTrue(changed)
        self.assertIs(adopted, channel)
        self.assertEqual(
            db.get_setting(f"managed_channel:{self.guild.id}:booster-lounge"),
            str(channel.id),
        )

    async def test_changed_native_booster_role_rejects_stale_review(self):
        preview = boosters.review_snapshot(self.guild)
        replacement = self.guild.role(5001)
        replacement.name = "Server Booster"
        replacement.managed = True
        self.guild.roles.append(replacement)
        self.guild.premium_subscriber_role = replacement

        with self.assertRaisesRegex(ValueError, "changed while this review was open"):
            await boosters.apply(self.guild, preview)

    def test_lounge_outside_community_requires_manual_review(self):
        other = self.guild.add_category("CUSTOM")
        self.guild.add_channel("booster-lounge", other)
        with self.assertRaisesRegex(Exception, "outside COMMUNITY"):
            boosters.status(self.guild)


if __name__ == "__main__":
    unittest.main()
