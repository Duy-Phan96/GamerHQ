"""Offline migration identity, bot groups, feed separation and private access."""
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import discord
import test_onboarding as fixtures
from database import db
from services import bot_group_service as groups, instant_gaming_service as ig
from services import support_service as support, managed_message_service as managed
from services.server_setup_service import repair_server
from services.channel_adoption_service import children
from services.health_service import scan


class KnownBotConfigurationTests(unittest.TestCase):
    def test_public_defaults_are_exact_integer_ids(self):
        import config
        for name, uid in config.THIRD_PARTY_BOTS.items():
            self.assertIs(type(uid), int)
            self.assertGreater(uid, 2**53)
            self.assertEqual(getattr(config, groups.CONFIG[name]), uid)


class BotOrganizationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    async def asyncSetUp(self):
        self.guild.mod.position = 100
        self.guild.me.top_role.position = 200
        self.members = {}
        for index, (name, config_name) in enumerate(groups.CONFIG.items(), 800):
            member = MagicMock(spec=discord.Member)
            member.id, member.name, member.bot, member.guild = index, name, True, self.guild
            member.roles, member.guild_permissions = [], discord.Permissions.none()
            async def add(*roles, member=member, **kwargs):
                member.roles.extend(r for r in roles if r not in member.roles)
            member.add_roles = AsyncMock(side_effect=add)
            self.members[name] = member
            patcher = patch('config.' + config_name, index)
            patcher.start(); self.addCleanup(patcher.stop)
        self.guild.members = list(self.members.values())
        self.guild.get_member = lambda mid: next((m for m in self.guild.members if m.id == mid), None)
        groups._locks.clear()
        groups._members.clear()
        groups._assigned.clear()

    async def test_setup_preserves_channels_adds_free_games_and_repeats(self):
        _, failed = await repair_server(self.guild, self.bot)
        self.assertEqual(failed, [])
        partners = support.resource(self.guild, 'partners-benefits', True)
        expected = ['gaming-news', 'gaming-deals', 'free-games', 'amazon', 'ai-tools', 'haushaltscheck']
        self.assertEqual([c.name for c in children(self.guild.channels, partners.id)], [support.PARTNER_CHANNELS[n] for n in expected])
        identities = {n: support.resource(self.guild, n).id for n in expected}
        pins = {n: list(support.resource(self.guild, n).messages) for n in expected}
        role_ids = [r.id for r in self.guild.roles]
        category_ids = [c.id for c in self.guild.categories]
        await repair_server(self.guild, self.bot)
        self.assertEqual(identities, {n: support.resource(self.guild, n).id for n in expected})
        self.assertEqual(pins, {n: list(support.resource(self.guild, n).messages) for n in expected})
        self.assertEqual(role_ids, [r.id for r in self.guild.roles])
        self.assertEqual(category_ids, [c.id for c in self.guild.categories])

    async def test_existing_private_channels_and_messages_migrate_in_place(self):
        old = {}
        for name in ('ig-purchases', 'ig-buyer-ranking'):
            channel = self.guild.add_channel(ig.CHANNELS[name][0].replace('・', '・ig-'), self.staff)
            db.set_setting(ig.channel_key(self.guild, name), channel.id)
            message = self.add_message(channel, ig.CHANNELS[name][1], pinned=True)
            db.set_setting(ig.message_key(self.guild, name), message.id)
            history = self.add_message(channel, 'existing private history', author=777)
            old[name] = (channel, message, history)
        original_staff = dict(self.staff.overwrites)
        await repair_server(self.guild, self.bot)
        category = ig.affiliate_category(self.guild)
        self.assertEqual(category.name, '🔒 AFFILIATE STATS')
        self.assertEqual(self.staff.overwrites, original_staff)
        for name, (channel, message, history) in old.items():
            self.assertIs(ig.resolve(self.guild, name), channel)
            self.assertEqual(channel.category_id, category.id)
            self.assertEqual(channel.name, ig.CHANNELS[name][0])
            self.assertEqual(db.get_setting(ig.message_key(self.guild, name)), str(message.id))
            self.assertIn(history.id, channel.messages)

    async def test_stats_inherited_privacy_and_identity_only_grants(self):
        await repair_server(self.guild, self.bot)
        category = ig.affiliate_category(self.guild)
        grouping = groups.resolve(self.guild, 'gaming')
        instant, gecko = self.members['instant-gaming'], self.members['dealgecko']
        for target in [category, ig.resolve(self.guild, 'ig-purchases'), ig.resolve(self.guild, 'ig-buyer-ranking')]:
            self.assertFalse(target.overwrites_for(self.guild.default_role).view_channel)
            self.assertTrue(target.overwrites_for(self.guild.mod).view_channel)
            self.assertTrue(target.overwrites_for(self.guild.mod).read_message_history)
            self.assertTrue(all(getattr(target.overwrites_for(instant), bit) for bit in ig.BOT_RIGHTS))
            self.assertIsNot(target.overwrites_for(grouping).view_channel, True)
            self.assertIsNot(target.overwrites_for(gecko).view_channel, True)
            for bit in ('administrator', 'manage_guild', 'manage_roles', 'manage_channels', 'kick_members', 'ban_members'):
                self.assertFalse(getattr(target.overwrites_for(instant), bit))
        self.assertEqual(ig.resolve(self.guild, 'ig-purchases').overwrites, category.overwrites)

    async def test_free_games_gift_copy_bot_access_and_editor(self):
        await repair_server(self.guild, self.bot)
        channel = support.resource(self.guild, 'free-games')
        self.assertEqual(channel.name, '🎁・free-games')
        self.assertFalse(channel.overwrites_for(self.guild.default_role).send_messages)
        self.assertTrue(channel.overwrites_for(self.guild.default_role).view_channel)
        self.assertTrue(channel.overwrites_for(self.guild.default_role).read_message_history)
        gecko = self.members['dealgecko']
        self.assertTrue(all(getattr(channel.overwrites_for(gecko), bit) for bit in ig.BOT_RIGHTS))
        self.assertTrue(ig.resolve(self.guild, 'gaming-deals').overwrites_for(gecko).send_messages)
        self.assertIsNot(ig.resolve(self.guild, 'gaming-news').overwrites_for(gecko).send_messages, True)
        key = support.message_key(self.guild, 'free_games')
        state = managed.load(key)
        self.assertEqual(state['content'], support.FREE_GAMES_TEXT)
        self.assertNotIn('affiliate', state['content'].lower())
        self.assertIn(key, managed.specs(self.guild))
        state.update(content='# 🎁 Free Games\nCustom useful information', customized=True)
        state['content_hash'] = managed.digest(state['content'])
        channel.messages[state['message_id']].content = state['content']; managed.store(state)
        await repair_server(self.guild, self.bot)
        self.assertEqual(channel.messages[state['message_id']].content, state['content'])

    async def test_group_roles_assign_known_bots_hoist_and_preserve_staff_order(self):
        await groups.sync(self.guild)
        for group, (_, names) in groups.GROUPS.items():
            role = groups.resolve(self.guild, group)
            self.assertTrue(role.hoist)
            self.assertEqual(role.permissions.value, 0)
            self.assertLess(role.position, self.guild.mod.position)
            for name in names: self.assertIn(role, self.members[name].roles)
        self.assertEqual(self.guild.mod.position, 100)
        self.assertGreater(groups.resolve(self.guild, 'gaming').position, groups.resolve(self.guild, 'music').position)

    async def test_unknown_bot_names_are_not_identity_and_higher_hoists_warn(self):
        with patch('config.DEALGECKO_BOT_ID', 0):
            await groups.sync(self.guild)
            self.members['dealgecko'].add_roles.assert_not_called()
            self.assertTrue(any(row[0] == 'dealgecko' and row[1] == 'WARN' for row in groups.diagnostics(self.guild)))
        higher = self.guild.role(444); higher.position, higher.hoist = 101, True
        self.members['instant-gaming'].roles.append(higher)
        self.assertTrue(any('grouping' in row[0] for row in groups.diagnostics(self.guild)))
        self.assertTrue(higher.hoist)

    async def test_health_readonly_optional_absence_and_carl_recommendation(self):
        await repair_server(self.guild, self.bot)
        carl = MagicMock(spec=discord.Member); carl.bot, carl.name = True, 'Carl-bot'
        self.guild.members.append(carl)
        with db.connect() as conn: before = list(conn.iterdump())
        with patch('config.DEALGECKO_BOT_ID', 0):
            findings = await scan(self.guild, self.bot, messages=False)
        self.assertTrue(any(f.name == 'free-games' and f.state == 'PASS' for f in findings))
        self.assertTrue(any(f.name == 'DealGecko free-games access' and f.state == 'WARN' for f in findings))
        self.assertTrue(any(f.name == 'Carl-bot' and f.state == 'WARN' for f in findings))
        with db.connect() as conn: self.assertEqual(before, list(conn.iterdump()))
        carl.kick.assert_not_called()

    async def test_duplicate_stats_category_and_unsafe_group_fail_closed(self):
        self.guild.add_category('🔒 AFFILIATE STATS'); self.guild.add_category('Affiliate Stats')
        from services.server_service import ServerMessageError
        with self.assertRaises(ServerMessageError): await ig.ensure_affiliate_category(self.guild)
        role = await self.guild.create_role(name='Gaming Bots', permissions=discord.Permissions(administrator=True))
        await groups.sync(self.guild)
        self.members['instant-gaming'].add_roles.assert_not_called()
        self.assertTrue(role.permissions.administrator)  # Existing grants are reported, not silently rewritten.

    async def test_existing_news_poster_grants_preserved_without_expansion(self):
        await repair_server(self.guild, self.bot)
        channel = ig.resolve(self.guild, 'gaming-news')
        publisher = MagicMock(spec=discord.Member)
        publisher.id, publisher.bot, publisher.roles = 12345, True, []
        channel.overwrites[publisher] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=False)
        original = channel.overwrites_for(publisher)
        await ig.sync(self.guild)
        self.assertEqual(channel.overwrites_for(publisher), original)

    def test_readme_exact_partner_order_and_private_stats(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8')
        self.assertIn('🤝 PARTNERS & BENEFITS\n├─ 📰・gaming-news\n├─ 🔥・gaming-deals\n├─ 🎁・free-games\n├─ 🛒・amazon\n├─ 🤖・ai-tools\n└─ 🇩🇪・haushaltscheck', text)
        self.assertIn('🔒 AFFILIATE STATS (private)\n├─ 💸・purchases\n└─ 🏆・buyer-ranking', text)

    async def test_known_ids_without_names_and_uncached_members(self):
        import config
        for name, uid in config.THIRD_PARTY_BOTS.items():
            self.members[name].id = uid
            self.members[name].name = 'unrelated display label'
            patcher = patch('config.' + groups.CONFIG[name], uid)
            patcher.start(); self.addCleanup(patcher.stop)
        self.guild.get_member = lambda uid: None
        async def fetch(uid):
            return next(m for m in self.members.values() if m.id == uid)
        self.guild.fetch_member = AsyncMock(side_effect=fetch)
        unrelated = self.guild.role(321)
        self.members['jockie'].roles.append(unrelated)
        notes = await groups.sync(self.guild)
        await groups.sync(self.guild)
        self.assertEqual(self.guild.fetch_member.await_count, 4)
        for group, (_, names) in groups.GROUPS.items():
            role = groups.resolve(self.guild, group)
            for name in names:
                self.assertIn(role, self.members[name].roles)
                self.members[name].add_roles.assert_awaited_once()
                self.assertTrue(any(groups.LABELS[name] in text and '✅' in text for text in notes))
        self.assertIn(unrelated, self.members['jockie'].roles)
        await repair_server(self.guild, self.bot)
        free = support.resource(self.guild, 'free-games')
        self.assertTrue(free.overwrites_for(self.members['dealgecko']).send_messages)
        stats = ig.affiliate_category(self.guild)
        self.assertTrue(stats.overwrites_for(self.members['instant-gaming']).view_channel)
        self.assertIsNot(stats.overwrites_for(self.members['dealgecko']).view_channel, True)

    async def test_missing_fetch_cached_and_other_assignments_continue(self):
        gecko = self.members['dealgecko']
        self.guild.members.remove(gecko)
        response = MagicMock(status=404, reason='Not Found')
        self.guild.fetch_member = AsyncMock(side_effect=discord.NotFound(response, 'missing'))
        notes = await groups.sync(self.guild)
        await groups.sync(self.guild)
        self.guild.fetch_member.assert_awaited_once_with(gecko.id)
        self.assertTrue(any('DealGecko not found' in text for text in notes))
        self.assertIn(groups.resolve(self.guild, 'music'), self.members['pancake'].roles)
        self.assertTrue(any('DealGecko →' in row[0] and row[1] == 'WARN' for row in groups.diagnostics(self.guild)))

    async def test_one_assignment_failure_does_not_skip_peer_or_leak_exception(self):
        response = MagicMock(status=403, reason='Forbidden')
        self.members['jockie'].add_roles.side_effect = discord.Forbidden(response, 'PRIVATE_ERROR_DETAIL')
        notes = await groups.sync(self.guild)
        self.assertIn(groups.resolve(self.guild, 'music'), self.members['pancake'].roles)
        self.assertNotIn('PRIVATE_ERROR_DETAIL', ' '.join(notes))
        self.assertTrue(any('Jockie Music assignment failed' in text for text in notes))

    async def test_existing_instant_mapping_preserved_for_group_and_private_access(self):
        db.set_setting(f'bot_member:{self.guild.id}:instant-gaming', self.members['instant-gaming'].id)
        with patch('config.INSTANT_GAMING_BOT_ID', 0):
            await repair_server(self.guild, self.bot)
            instant = self.members['instant-gaming']
            self.assertIn(groups.resolve(self.guild, 'gaming'), instant.roles)
            self.assertTrue(ig.affiliate_category(self.guild).overwrites_for(instant).send_messages)

    async def test_health_explicit_success_rows_and_no_fetch(self):
        await groups.sync(self.guild)
        self.guild.fetch_member = AsyncMock()
        rows = groups.diagnostics(self.guild)
        for label in groups.LABELS.values():
            self.assertTrue(any(row[0].startswith(label + ' →') and row[1] == 'PASS' for row in rows))
        self.guild.fetch_member.assert_not_called()

    async def test_deals_role_access_parent_denies_and_unrelated_overwrites(self):
        await repair_server(self.guild, self.bot)
        deals = ig.resolve(self.guild, 'gaming-deals')
        gecko = self.members['dealgecko']
        role = groups.resolve(self.guild, 'gaming')
        custom = self.guild.role(987)
        custom.name = 'Custom interaction'
        original = discord.PermissionOverwrite(view_channel=False, send_messages=True, attach_files=False)
        deals.overwrites[custom] = original
        deals.category.overwrites[custom] = original
        for target in (role, gecko):
            deals.overwrites[target] = discord.PermissionOverwrite(view_channel=False, send_messages=False)
            deals.category.overwrites[target] = discord.PermissionOverwrite(view_channel=False)
        await repair_server(self.guild, self.bot)
        for target in (role, gecko):
            self.assertTrue(all(getattr(deals.overwrites_for(target), bit) is True for bit in ig.BOT_RIGHTS))
            self.assertTrue(deals.category.overwrites_for(target).view_channel)
            self.assertFalse(deals.overwrites_for(target).administrator)
            self.assertIsNot(ig.affiliate_category(self.guild).overwrites_for(target).view_channel, True)
        self.assertEqual(deals.overwrites_for(custom), original)
        self.assertEqual(deals.category.overwrites_for(custom), original)
        before = dict(deals.overwrites)
        await repair_server(self.guild, self.bot)
        self.assertEqual(deals.overwrites, before)
        deals.permissions_for = lambda target: discord.Permissions.none() if target == gecko else discord.Permissions.all()
        findings = await scan(self.guild, self.bot, messages=False)
        self.assertTrue(any(f.name == 'DealGecko gaming-deals access' and f.state == 'WARN' for f in findings))
