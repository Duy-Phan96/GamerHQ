"""Offline integration tests for channel identity, private access and managed pins."""
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock
import discord
import test_onboarding as fixtures
from database import db
from services import instant_gaming_service as ig
from services.server_service import ServerMessageError


class InstantGamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.OnboardingTests.setUp(self)
        self.partners = self.guild.add_category('🛒 MARKETPLACE')
    add_message = fixtures.OnboardingTests.add_message

    def member(self, mid=901, *, bot=True):
        member = MagicMock(spec=discord.Member)
        member.id, member.bot, member.guild = mid, bot, self.guild
        member.roles = []
        patcher = patch('config.INSTANT_GAMING_BOT_ID', mid)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.guild.get_member = lambda value: member if value == member.id else None
        return member

    async def test_repeated_sync_persists_ids_and_pins_and_private_access(self):
        bot = self.member()
        await ig.sync(self.guild)
        ids = [c.id for c in self.guild.channels]
        await ig.sync(self.guild)
        self.assertEqual(ids, [c.id for c in self.guild.channels])
        for name, (_, content) in ig.CHANNELS.items():
            channel = ig.resolve(self.guild, name)
            self.assertIs(channel.category, self.partners if name in ig.PUBLIC else ig.affiliate_category(self.guild))
            self.assertEqual(channel.sends, 1)
            msg = channel.messages[int(db.get_setting(ig.message_key(self.guild, name)))]
            self.assertTrue(msg.pinned)
            self.assertEqual(msg.content, content)
            everyone = channel.overwrites_for(self.guild.default_role)
            self.assertEqual(everyone.view_channel, name in ig.PUBLIC)
            self.assertFalse(everyone.send_messages)
            for target in (bot, self.guild.me):
                self.assertTrue(all(getattr(channel.overwrites_for(target), right) for right in ig.BOT_RIGHTS))
            self.assertTrue(channel.overwrites_for(self.guild.mod).view_channel)
        self.assertIs(ig.configured_bot(self.guild), bot)

    async def test_adopts_alias_anywhere_and_removes_public_grants_only_on_targets(self):
        other = self.guild.add_channel('unrelated', self.community)
        other.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        before = other.overwrites.copy()
        purchases = self.guild.add_channel('💸・affiliate-purchases', self.community)
        purchases.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=False)
        user = self.member(777, bot=False)
        purchases.overwrites[user] = discord.PermissionOverwrite(view_channel=True)
        note = self.add_message(purchases, 'User pin', author=777, pinned=True)
        await ig.sync(self.guild)
        self.assertIs(ig.resolve(self.guild, 'ig-purchases'), purchases)
        self.assertIs(purchases.category, ig.affiliate_category(self.guild))
        self.assertFalse(purchases.overwrites_for(self.guild.custom).view_channel)
        self.assertFalse(purchases.overwrites_for(user).view_channel)
        self.assertIs(purchases.overwrites_for(self.guild.custom).attach_files, False)
        self.assertEqual(other.overwrites, before)
        self.assertFalse(note.deleted)
        self.assertEqual(note.edits, 0)

    async def test_ambiguous_names_do_not_mutate_or_create(self):
        self.guild.add_channel('ig-purchases', self.staff)
        self.guild.add_channel('affiliate-purchases', self.community)
        before = list(self.guild.channels)
        with self.assertRaises(ServerMessageError):
            await ig.sync(self.guild)
        self.assertEqual(self.guild.channels, before)
        self.assertIsNone(db.get_setting(ig.channel_key(self.guild, 'gaming-news')))

    async def test_mapped_renamed_channel_wins_and_bot_rotation_revokes_previous_access(self):
        old = self.member()
        await ig.sync(self.guild)
        purchases = ig.resolve(self.guild, 'ig-purchases')
        purchases.name = 'custom-name'
        lookalike = self.guild.add_channel('ig-purchases', self.community)
        new = self.member(902)
        await ig.sync(self.guild)
        self.assertIs(ig.resolve(self.guild, 'ig-purchases'), purchases)
        self.assertFalse(purchases.overwrites_for(old).view_channel)
        self.assertTrue(purchases.overwrites_for(new).send_messages)
        self.assertEqual(lookalike.overwrites, {})

    async def test_human_rejected_and_unconfigured_bot_reported(self):
        self.member(bot=False)
        self.assertIsNone(ig.configured_bot(self.guild))
        result = await ig.sync(self.guild)
        self.assertIn('pending', result)
        rows = await ig.diagnostics(self.guild, messages=False)
        self.assertTrue(any(name == 'Instant Gaming bot' and state == 'WARN' for name, state, _ in rows))

    async def test_refresh_is_mapping_only_and_deleted_pin_recovers(self):
        await ig.refresh(self.guild)
        self.assertIsNone(ig.resolve(self.guild, 'gaming-news'))
        await ig.sync(self.guild)
        news = ig.resolve(self.guild, 'gaming-news')
        mid = int(db.get_setting(ig.message_key(self.guild, 'gaming-news')))
        del news.messages[mid]
        await ig.refresh(self.guild)
        self.assertEqual(len(news.messages), 1)
        purchases = ig.resolve(self.guild, 'ig-purchases')
        purchases.overwrites[self.guild.default_role].view_channel = True
        before = sum(m.edits for m in purchases.messages.values())
        await ig.refresh(self.guild)
        self.assertEqual(sum(m.edits for m in purchases.messages.values()), before)
        rows = await ig.diagnostics(self.guild, messages=False)
        self.assertTrue(any(name == 'ig-purchases permissions' and state == 'REPAIRABLE' for name, state, _ in rows))

    async def test_concurrent_sync_and_missing_staff(self):
        await asyncio.gather(ig.sync(self.guild), ig.sync(self.guild))
        for name in ig.CHANNELS:
            self.assertEqual(ig.resolve(self.guild, name).sends, 1)
        self.guild.categories.remove(self.staff)
        await ig.sync(self.guild)
        self.assertIs(ig.resolve(self.guild, 'ig-purchases').category, ig.affiliate_category(self.guild))

    async def test_setup_inventory_and_health_include_both_channels(self):
        from services.server_setup_service import repair_server, analyze_server
        from services.health_service import scan
        await repair_server(self.guild, self.bot)
        inventory = analyze_server(self.guild)
        self.assertTrue(all(any(row['channel'] == ig.resolve(self.guild, name)
                               for group in inventory['categories'] for row in group['channels']) for name in ig.CHANNELS))
        rows = await scan(self.guild, messages=False)
        self.assertTrue(all(any(f.name == name and f.state == 'PASS' for f in rows) for name in ig.CHANNELS))

    async def test_news_editor_customization_survives_sync_and_purchase_pin_stays_private(self):
        from services import managed_message_service as managed
        await ig.sync(self.guild)
        admin = SimpleNamespace(id=5, guild_permissions=discord.Permissions(administrator=True))
        self.guild.owner_id = 5
        self.guild.get_member = lambda mid: admin if mid == 5 else None
        key = ig.message_key(self.guild, 'gaming-news')
        state = managed.load(key)
        state['content'] = '# Custom gaming updates'
        await managed.save(self.guild, admin, state, confirmed=True)
        await ig.sync(self.guild)
        news = ig.resolve(self.guild, 'gaming-news')
        self.assertEqual(news.messages[int(db.get_setting(key))].content, '# Custom gaming updates')
        self.assertEqual(news.sends, 1)
        self.assertNotIn(ig.message_key(self.guild, 'ig-purchases'), managed.specs(self.guild))

    async def test_explicit_staff_member_override_keeps_staff_access_and_news_is_in_partners(self):
        staff = self.member(bot=False)
        staff.roles = [self.guild.mod]
        purchases = self.guild.add_channel('ig-purchases', self.staff)
        purchases.overwrites[staff] = discord.PermissionOverwrite(view_channel=False)
        announcement = next(c for c in self.start.text_channels if c.name == 'announcements')
        announcement.position = 2
        await ig.sync(self.guild)
        self.assertTrue(purchases.overwrites_for(staff).view_channel)
        news = ig.resolve(self.guild, 'gaming-news')
        self.assertIs(news.category, self.partners)
        self.assertFalse(any('position' in edit for edit in news.edits))

    async def test_edit_return_used_before_gateway_cache_catches_up(self):
        purchases = self.guild.add_channel('ig-purchases', self.staff)
        purchases.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=True)
        updated = copy.copy(purchases)
        async def edit(**kwargs):
            updated.overwrites = kwargs['overwrites']
            return updated
        purchases.edit = AsyncMock(side_effect=edit)
        await ig.sync(self.guild)
        self.assertTrue(purchases.overwrites_for(self.guild.default_role).view_channel)
        self.assertFalse(updated.overwrites_for(self.guild.default_role).view_channel)
        self.assertEqual(len(updated.messages), 1)
        self.assertTrue(next(iter(updated.messages.values())).pinned)

    async def test_deleted_channels_and_pins_recover_for_all_four(self):
        await ig.sync(self.guild)
        for name in ig.CHANNELS:
            channel = ig.resolve(self.guild, name)
            mid = int(db.get_setting(ig.message_key(self.guild, name)))
            del channel.messages[mid]
        await ig.sync(self.guild)
        for name in ig.CHANNELS:
            self.assertEqual(len(ig.resolve(self.guild, name).messages), 1)
        original = {name: ig.resolve(self.guild, name).id for name in ig.CHANNELS}
        for name in ig.CHANNELS:
            self.guild.text_channels.remove(ig.resolve(self.guild, name))
        await ig.sync(self.guild)
        for name in ig.CHANNELS:
            self.assertNotEqual(ig.resolve(self.guild, name).id, original[name])
            self.assertEqual(ig.resolve(self.guild, name).sends, 1)

    async def test_permission_drift_and_independent_stats_category_policy(self):
        bot = self.member()
        self.staff.overwrites[self.guild.mod] = discord.PermissionOverwrite(send_messages=False)
        await ig.sync(self.guild)
        for name in set(ig.CHANNELS) - ig.PUBLIC:
            channel = ig.resolve(self.guild, name)
            self.assertFalse(channel.overwrites_for(self.guild.mod).send_messages)
            self.assertTrue(channel.overwrites_for(bot).send_messages)
            channel.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True)
            channel.overwrites[bot].embed_links = False
        self.staff.overwrites[self.guild.mod].send_messages = True
        await ig.sync(self.guild)
        for name in set(ig.CHANNELS) - ig.PUBLIC:
            channel = ig.resolve(self.guild, name)
            self.assertFalse(channel.overwrites_for(self.guild.custom).view_channel)
            self.assertFalse(channel.overwrites_for(self.guild.mod).send_messages)
            self.assertTrue(channel.overwrites_for(bot).embed_links)

    async def test_health_reports_duplicates_without_touching_manual_pins(self):
        await ig.sync(self.guild)
        news = ig.resolve(self.guild, 'gaming-news')
        duplicate = self.add_message(news, ig.CHANNELS['gaming-news'][1], pinned=True)
        manual = self.add_message(news, 'Manual announcement', author=20, pinned=True)
        self.guild.add_channel('gaming-news', self.community)
        rows = await ig.diagnostics(self.guild)
        for label in ('gaming-news duplicates', 'gaming-news duplicate pins'):
            self.assertTrue(any(name == label and state == 'MANUAL_REVIEW' for name, state, _ in rows))
        self.assertFalse(manual.deleted or duplicate.deleted)

    async def test_old_deals_pin_id_and_partner_sync_are_preserved(self):
        from services import support_service as support
        from services.server_setup_service import repair_server
        channel = self.guild.add_channel('🎮・gaming-deals', self.start)
        old = self.add_message(channel, '# 🎮 Gaming Deals\n\nOld default', pinned=True)
        db.set_setting(ig.channel_key(self.guild, 'gaming-deals'), channel.id)
        db.set_setting(ig.message_key(self.guild, 'gaming-deals'), old.id)
        await ig.sync(self.guild)
        self.assertIs(channel.category, self.partners)
        self.assertEqual(channel.name, '🔥・gaming-deals')
        self.assertEqual(old.content, ig.CHANNELS['gaming-deals'][1])
        self.assertEqual(channel.sends, 0)
        await repair_server(self.guild, self.bot)
        await support.sync_support_messages(self.guild)
        self.assertIs(channel.category, self.partners)
        self.assertEqual(len(channel.messages), 1)
        self.assertEqual(db.get_setting(ig.message_key(self.guild, 'gaming-deals')), str(old.id))
        self.assertEqual(old.view.children[0].url, support.AFFILIATES[0].url)

    async def test_missing_id_logs_warning_and_result_covers_four_channels(self):
        with patch('config.INSTANT_GAMING_BOT_ID', 0), self.assertLogs(ig.log, level='WARNING') as logs:
            result = await ig.sync(self.guild)
        self.assertIn('ID missing', logs.output[0])
        for display, _ in ig.CHANNELS.values():
            self.assertIn(display, result)
        self.assertIn('Bot ID not configured', result)

    async def test_migration_orders_managed_partners_with_feeds_first(self):
        from services import support_service as support
        existing = []
        for name in ('amazon', 'ai-tools', 'electricity'):
            channel = self.guild.add_channel(name, self.partners)
            db.set_setting(support.channel_key(self.guild, name), channel.id)
            existing.append(channel.id)
        for name in ('gaming-deals', 'gaming-news'):
            channel = self.guild.add_channel(ig.CHANNELS[name][0], self.start)
            channel.position = 0
            db.set_setting(ig.channel_key(self.guild, name), channel.id)
        original = {name: ig.resolve(self.guild, name).id for name in ig.PUBLIC}
        await ig.sync(self.guild)
        ordered = sorted(self.partners.text_channels, key=lambda c: (c.position, c.id))
        self.assertEqual([c.id for c in ordered], [original['gaming-news'], original['gaming-deals']] + existing)
        self.assertEqual(len(self.guild.position_updates), 1)
        self.assertEqual({entry['id'] for entry in self.guild.position_updates[0]}, set(original.values()) | set(existing))
        await ig.sync(self.guild)
        self.assertEqual(len(self.guild.position_updates), 1)
        self.assertEqual({name: ig.resolve(self.guild, name).id for name in ig.PUBLIC}, original)
        everyone = self.partners.overwrites_for(self.guild.default_role)
        self.assertTrue(everyone.view_channel and everyone.read_message_history)
        self.assertFalse(everyone.send_messages)

    async def test_unknown_interactive_channels_are_preserved(self):
        interactive = self.guild.add_channel('partner-chat', self.partners)
        interactive.overwrites[self.guild.default_role] = discord.PermissionOverwrite(send_messages=True)
        before = copy.deepcopy(interactive.overwrites)
        await ig.sync(self.guild)
        self.assertEqual(interactive.overwrites, before)
        self.assertFalse(self.partners.overwrites_for(self.guild.default_role).send_messages)

    async def test_unknown_synced_child_prevents_category_propagation_only(self):
        interactive = self.guild.add_channel('partner-chat', self.partners)
        await ig.sync(self.guild)
        self.assertEqual(interactive.overwrites, {})
        self.assertEqual(self.partners.overwrites, {})
        self.assertFalse(ig.resolve(self.guild, 'gaming-news').overwrites_for(self.guild.default_role).send_messages)
        rows = await ig.diagnostics(self.guild, messages=False)
        self.assertIn(('Partner category permissions', 'REPAIRABLE'), [(name, state) for name, state, _ in rows])

    async def test_health_detects_public_placement_and_permissions_without_mutation(self):
        await ig.sync(self.guild)
        news = ig.resolve(self.guild, 'gaming-news')
        deals = ig.resolve(self.guild, 'gaming-deals')
        news.category = self.start
        deals.overwrites[self.guild.default_role].send_messages = True
        before = len(news.edits) + len(deals.edits)
        rows = await ig.diagnostics(self.guild, messages=False)
        states = {name: state for name, state, _ in rows}
        self.assertEqual(states['gaming-news'], 'REPAIRABLE')
        self.assertEqual(states['gaming-deals permissions'], 'REPAIRABLE')
        self.assertEqual(states['ig-purchases permissions'], 'PASS')
        self.assertEqual(states['ig-buyer-ranking permissions'], 'PASS')
        self.assertEqual(len(news.edits) + len(deals.edits), before)
