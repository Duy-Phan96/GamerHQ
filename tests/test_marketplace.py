"""Marketplace migration keeps persisted identities and fails closed on ambiguity."""
import json
import unittest
from unittest.mock import AsyncMock, patch
import discord
import test_onboarding as fixtures
from database import db
from services import support_service as support, managed_message_service as managed
from services.server_service import ServerMessageError
from services.server_setup_service import repair_server


class MarketplaceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    async def legacy_board(self):
        await repair_server(self.guild, self.bot)
        channel = support.resource(self.guild, 'electricity')
        category = channel.category
        category.name = '🤝 PARTNERS & BENEFITS'
        channel.name = '🇩🇪・haushaltscheck'
        db.set_setting(support.channel_key(self.guild, 'haushaltscheck'), channel.id)
        with db.connect() as conn:
            conn.execute('DELETE FROM settings WHERE key=?', (support.channel_key(self.guild, 'electricity'),))
        return channel, category

    async def test_migration_reuses_category_channel_pin_and_history_twice(self):
        channel, category = await self.legacy_board()
        key = support.message_key(self.guild, 'household')
        pin_id = db.get_setting(key)
        note = self.add_message(channel, 'Manual note', author=123, pinned=True)
        others = {name: support.resource(self.guild, name).id for name in support.PARTNER_CHANNELS if name != 'electricity'}
        old = '# 🇩🇪 Haushaltscheck\n\nLegacy default'
        state = managed.load(key)
        state.update(content=old, default_content=old, content_hash=managed.digest(old))
        managed.store(state)
        channel.messages[int(pin_id)].content = old
        db.set_setting(f'managed_channel_state:{self.guild.id}:haushaltscheck', json.dumps(dict(channel_id=channel.id, name=channel.name, category=category.id)))
        for _ in range(2):
            await support.repair_support(self.guild, [])
        self.assertEqual(category.name, '🛒 MARKETPLACE')
        self.assertIs(support.resource(self.guild, 'partners-benefits', True), category)
        self.assertIs(support.resource(self.guild, 'electricity'), channel)
        self.assertEqual(channel.name, '🇩🇪・electricity')
        self.assertEqual(db.get_setting(key), pin_id)
        self.assertEqual(channel.messages[int(pin_id)].content, support.ELECTRICITY_TEXT)
        self.assertFalse(note.deleted)
        self.assertEqual(note.edits, 0)
        self.assertEqual(channel.sends, 1)
        self.assertIsNone(db.get_setting(support.channel_key(self.guild, 'haushaltscheck')))
        self.assertIsNone(db.get_setting(f'managed_channel_state:{self.guild.id}:haushaltscheck'))
        self.assertEqual(others, {name: support.resource(self.guild, name).id for name in others})
        self.assertEqual([c.name for c in sorted(category.text_channels, key=lambda c: (c.position,c.id))], list(support.PARTNER_CHANNELS.values()))

    async def test_conflicting_category_or_channel_is_never_merged(self):
        channel, category = await self.legacy_board()
        duplicate = self.guild.add_category('🛒 MARKETPLACE')
        with self.assertRaises(ServerMessageError):
            await support.repair_support(self.guild, [])
        self.assertEqual(category.name, '🤝 PARTNERS & BENEFITS')
        self.guild.categories.remove(duplicate)
        self.guild.add_channel('🇩🇪・electricity', category)
        with self.assertRaises(ServerMessageError):
            await support.repair_support(self.guild, [])
        self.assertEqual(channel.name, '🇩🇪・haushaltscheck')

    async def test_failed_rename_retries_using_persisted_id(self):
        channel, category = await self.legacy_board()
        error = discord.Forbidden(type('Response', (), {'status':403, 'reason':'Forbidden'})(), 'denied')
        with patch.object(channel, 'edit', AsyncMock(side_effect=error)):
            with self.assertRaises(discord.Forbidden):
                await support.repair_support(self.guild, [])
        self.assertEqual(db.get_setting(support.channel_key(self.guild, 'electricity')), str(channel.id))
        await support.repair_support(self.guild, [])
        self.assertIs(support.resource(self.guild, 'electricity'), channel)
        self.assertEqual(channel.sends, 1)

    async def test_custom_legacy_pin_is_preserved_and_reported(self):
        from services.health_service import scan
        channel, _ = await self.legacy_board()
        key = support.message_key(self.guild, 'household')
        state = managed.load(key)
        old = '# 🇩🇪 Haushaltscheck\n\nCustom owner note'
        state.update(content=old, content_hash=managed.digest(old), customized=True)
        state['buttons'][0]['target'] = 'HOUSEHOLD_CHECK_REQUEST'
        managed.store(state)
        channel.messages[state['message_id']].content = old
        await support.repair_support(self.guild, [])
        self.assertEqual(managed.load(key)['content'], old)
        self.assertEqual(channel.messages[state['message_id']].content, old)
        view = managed.render(managed.load(key)['buttons'])
        self.assertTrue(view.children[0].disabled)
        self.assertTrue(view.is_persistent())
        findings = await scan(self.guild, self.bot)
        self.assertTrue(any(f.name == 'Electricity legacy customization' and f.state == 'MANUAL_REVIEW' for f in findings))

    async def test_legacy_intentional_removal_is_preserved(self):
        channel, _ = await self.legacy_board()
        db.set_setting(f'managed_channel_removed:{self.guild.id}:haushaltscheck', '1')
        before = len(self.guild.text_channels)
        await support.repair_support(self.guild, [])
        self.assertEqual(len(self.guild.text_channels), before)
        self.assertIsNone(support.resource(self.guild, 'electricity'))
        self.assertIsNone(db.get_setting(f'managed_channel_removed:{self.guild.id}:haushaltscheck'))
        self.assertEqual(db.get_setting(f'managed_channel_removed:{self.guild.id}:electricity'), '1')

    def test_exact_english_copy(self):
        self.assertEqual(support.ELECTRICITY_TEXT, "# 🇩🇪 Electricity\n\nAvailable for users in Germany.\n\nLooking for a better electricity tariff?\n\nYou can compare several suitable options through our partner and decide for yourself which one works best for you.")
        for term in ('haushaltscheck', 'finance', 'gas', 'kfz', 'investment', 'insurance'):
            self.assertNotIn(term, support.ELECTRICITY_TEXT.lower())
