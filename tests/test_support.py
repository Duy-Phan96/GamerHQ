"""Affiliate-only support board: identity, permissions, canonical pins and copy."""
import unittest
from dataclasses import replace
from unittest.mock import patch
import discord
import test_onboarding as fixtures
from database import db
from services import support_service as support
from services import community_structure_service as structure
from services.server_setup_service import repair_server
from services.server_service import ServerMessageError
from services.onboarding_service import alias


class SupportTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    async def test_setup_twice_creates_one_public_read_only_board_and_pin(self):
        await repair_server(self.guild,self.bot)
        category=self.start; channel=support.resolve(self.guild,'channel')
        self.assertIs(category,self.start)
        self.assertEqual(channel.name,support.CHANNEL_NAME)
        self.assertEqual(channel.category_id,category.id)
        ids=[c.id for c in self.guild.channels]
        await repair_server(self.guild,self.bot)
        self.assertEqual(ids,[c.id for c in self.guild.channels])
        self.assertEqual(channel.sends,1)
        everyone=channel.overwrites_for(self.guild.default_role)
        self.assertTrue(everyone.view_channel and everyone.read_message_history)
        self.assertFalse(everyone.send_messages)
        self.assertFalse(everyone.create_public_threads)
        self.assertFalse(everyone.create_private_threads)
        self.assertFalse(everyone.send_messages_in_threads)
        for target in (self.guild.mod,self.guild.me):
            self.assertTrue(channel.overwrites_for(target).send_messages)
            self.assertTrue(channel.overwrites_for(target).manage_messages)
        message=next(iter(channel.messages.values()))
        self.assertTrue(message.pinned)
        self.assertEqual(message.content,support.support_text())

    async def test_equivalent_channel_reused_moved_and_history_preserved(self):
        category=self.guild.add_category('Support GamerHQ')
        channel=self.guild.add_channel('support-gamerhq',category)
        legacy_name=category.name
        canonical=self.add_message(channel,support.TITLE+'\nOld affiliate board',pinned=True)
        db.set_setting(support.message_key(self.guild),canonical.id)
        conversation=self.add_message(channel,'Existing user content',author=20)
        channel.overwrites[self.guild.default_role]=discord.PermissionOverwrite(use_application_commands=True)
        await repair_server(self.guild,self.bot)
        self.assertIs(support.resolve(self.guild,'category'),category)
        self.assertIs(support.resolve(self.guild,'channel'),channel)
        self.assertIs(channel.category,self.start)
        self.assertFalse(conversation.deleted)
        self.assertEqual(category.name,legacy_name)
        self.assertEqual(canonical.content,support.support_text())
        self.assertEqual(channel.sends,0)
        self.assertTrue(channel.overwrites_for(self.guild.default_role).use_application_commands)

    async def test_id_based_rename_and_placement_repair(self):
        await repair_server(self.guild,self.bot)
        category=self.start;channel=support.resolve(self.guild,'channel')
        channel.name='renamed-board';channel.category=self.community
        await repair_server(self.guild,self.bot)
        self.assertIs(category,self.start)
        self.assertEqual(channel.name,support.CHANNEL_NAME)
        self.assertIs(channel.category,self.start)
        self.assertEqual(channel.sends,1)

    async def test_managed_copy_updates_and_lost_id_recovers_without_touching_other_pins(self):
        await repair_server(self.guild,self.bot)
        channel=support.resolve(self.guild,'channel')
        canonical=next(iter(channel.messages.values()))
        user=self.add_message(channel,support.TITLE+'\nUser content',author=20,pinned=True)
        unrelated=self.add_message(channel,'Unrelated bot announcement',pinned=True)
        db.set_setting(support.message_key(self.guild),'')
        canonical.content=support.TITLE+'\nOld managed copy'
        await repair_server(self.guild,self.bot)
        self.assertEqual(canonical.content,support.support_text())
        self.assertEqual(channel.sends,1)
        self.assertFalse(user.deleted or unrelated.deleted)
        self.assertEqual(user.edits,0)
        self.assertEqual(unrelated.edits,0)

    async def test_guide_reference_short_no_links_and_message_size_safe(self):
        await repair_server(self.guild,self.bot)
        text=structure.guide_text(self.guild)
        channel=support.resolve(self.guild,'channel')
        self.assertTrue(text.endswith(support.guide_reference(self.guild)))
        self.assertIn(channel.mention,support.guide_reference(self.guild))
        self.assertLess(len(text)+200,2000)  # Reserve space for full Discord snowflakes.
        for entry in support.AFFILIATES:self.assertNotIn(entry.url,text)

    def test_config_urls_and_transparency_without_payments_or_tracking_claims(self):
        self.assertEqual([a.url for a in support.AFFILIATES],[
            'https://www.instant-gaming.com/?igr=gamer-0a9671a',
            'https://motivaiprivatelimited.sjv.io/c/7668488/3811144/49478',
            'https://amzn.to/4dnxPXh'])
        text=support.support_text()
        self.assertLess(len(text),2000)
        for entry in support.AFFILIATES:self.assertEqual(text.count(entry.url),1)
        for forbidden in ('paypal','donat','tip button','ko-fi','streamlabs','90 day','90-day','guaranteed','subscription'):
            self.assertNotIn(forbidden,text.lower())
        self.assertIn('browser bookmark',text)
        self.assertIn('completely optional',text)
        self.assertIn('may receive a commission',text)
        with patch.object(support,'AFFILIATES',(replace(support.AFFILIATES[0],active=False),)):
            self.assertNotIn(support.AFFILIATES[0].url,support.support_text())

    async def test_conflicting_names_and_private_source_fail_closed(self):
        self.guild.add_channel('support-gamerhq',self.staff)
        before=[c.id for c in self.guild.channels]
        with self.assertRaises(ServerMessageError):await support.repair_support(self.guild,[])
        self.assertEqual(before,[c.id for c in self.guild.channels])
        self.guild.add_channel('support-gamerhq',self.community)
        with self.assertRaises(ServerMessageError):await support.repair_support(self.guild,[])
        self.assertFalse(any(alias(c.name)=='support-gamerhq' for c in self.guild.categories))

    async def test_startup_does_not_create_support_structure(self):
        before=[c.id for c in self.guild.channels]
        await support.refresh_support(self.guild)
        self.assertEqual(before,[c.id for c in self.guild.channels])


if __name__=='__main__':unittest.main()
