"""Offline characterization of order planning and role administration boundaries."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import test_onboarding as fixtures
from database import db
from services import channel_adoption_service as adoption, support_service as support
from services.server_service import ServerMessageError
from cogs import server


class OrderPlanTests(unittest.TestCase):
    setUp = fixtures.OnboardingTests.setUp

    def board(self):
        category = self.guild.add_category(support.PARTNER_CATEGORY)
        db.set_setting(f'managed_category:{self.guild.id}:partners-benefits', category.id)
        boards = {}
        for name, display in adoption.supported().items():
            channel = self.guild.add_channel(display, category if name != 'support-gamerhq' else self.start)
            db.set_setting(support.channel_key(self.guild, name), channel.id)
            boards[name] = channel
        return category, boards

    def plan(self):
        return {current[0].category_id: [c.id for c in ordered] for current, ordered in adoption.order_plans(self.guild, self.guild.channels) if current}

    def test_default_order_preserves_manual_children_and_makes_no_writes(self):
        category, boards = self.board()
        first = self.guild.add_channel('custom-one', category)
        second = self.guild.add_channel('custom-two', category)
        first.position, second.position = 0, 1
        with patch.object(db, 'set_setting', side_effect=AssertionError('read only')):
            self.assertEqual(self.plan()[category.id], [boards[n].id for n in support.PARTNER_CHANNELS] + [first.id, second.id])

    def test_adopted_category_positions_and_free_games_adjacency(self):
        category, boards = self.board()
        moved = boards['amazon']
        moved.category = self.community
        db.set_setting(adoption.key(self.guild, 'amazon'), json.dumps(dict(channel_id=moved.id, category=self.community.id, position=0)))
        news = boards['gaming-news']
        db.set_setting(adoption.key(self.guild, 'gaming-news'), json.dumps(dict(channel_id=news.id, position=99)))
        result = self.plan()
        self.assertEqual(result[self.community.id][0], moved.id)
        self.assertEqual(result[category.id][-1], news.id)
        self.assertEqual(result[category.id][:2], [boards['gaming-deals'].id, boards['free-games'].id])
        db.set_setting(adoption.key(self.guild, 'gaming-news'), '')
        self.assertEqual(self.plan()[category.id][0], news.id)  # No state cached across calls.

    def test_mismatched_adopted_id_and_duplicate_mapping_fail_closed(self):
        _, boards = self.board()
        db.set_setting(adoption.key(self.guild, 'amazon'), json.dumps(dict(channel_id=999999, position=0)))
        with self.assertRaises(ServerMessageError): self.plan()
        db.set_setting(adoption.key(self.guild, 'amazon'), '')
        db.set_setting(support.channel_key(self.guild, 'amazon'), boards['ai-tools'].id)
        with self.assertRaises(ServerMessageError): self.plan()

    def test_measure_order_plan_connections(self):
        self.board()
        with patch.object(db, 'connect', wraps=db.connect) as connect:
            self.plan()
        self.assertEqual(connect.call_count, 2)  # Previously 15 for these seven boards.


class RoleSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.admin = SimpleNamespace(id=2, guild_permissions=discord.Permissions(administrator=True))
        self.members = {2:self.admin}
        self.guild = SimpleNamespace(id=1, owner_id=3, get_member=self.members.get)

    def views(self):
        return [server.RoleAdminView(guild=self.guild,admin_id=2),
                server.ConfirmRoleSyncView(guild=self.guild,admin_id=2),
                server.RoleCleanupSelectView(guild=self.guild,admin_id=2,candidates=[]),
                server.ConfirmRoleCleanupView(guild=self.guild,admin_id=2,candidates=[],selected_ids=set())]

    def interaction(self):
        return SimpleNamespace(guild=self.guild, user=self.admin, response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), is_done=lambda:False), followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    async def test_current_admin_allowed_and_other_actor_denied(self):
        for view in self.views():
            self.assertTrue(await view.interaction_check(self.interaction()))
            other = self.interaction(); other.user=SimpleNamespace(id=4)
            self.assertFalse(await view.interaction_check(other))
            self.assertTrue(other.response.send_message.call_args.kwargs['ephemeral'])

    async def test_revoked_or_departed_admin_cannot_reuse_role_session(self):
        for member in (SimpleNamespace(id=2, guild_permissions=discord.Permissions()), None):
            self.members[2] = member
            for view in self.views():
                self.assertFalse(await view.interaction_check(self.interaction()))

    async def test_direct_confirmation_rechecks_before_mutating(self):
        self.members[2] = None
        with patch('services.role_service.ensure_base_roles', new_callable=AsyncMock) as sync, patch('services.role_service.delete_cleanup_candidates', new_callable=AsyncMock) as delete:
            for view in self.views():
                if hasattr(view, 'confirm'):
                    await view.confirm.callback(self.interaction())
            sync.assert_not_awaited()
            delete.assert_not_awaited()

    async def test_permission_revoked_during_defer_stops_confirmation(self):
        for view in self.views():
            if not hasattr(view, 'confirm'):
                continue
            self.members[2] = self.admin
            interaction = self.interaction()
            async def revoke(**kwargs):
                self.members[2] = None
                interaction.response.is_done = lambda:True
            interaction.response.defer.side_effect = revoke
            with patch('services.role_service.ensure_base_roles', new_callable=AsyncMock) as sync, patch('services.role_service.delete_cleanup_candidates', new_callable=AsyncMock) as delete:
                await view.confirm.callback(interaction)
                sync.assert_not_awaited()
                delete.assert_not_awaited()
            interaction.followup.send.assert_awaited_once()

    async def test_wrong_guild_is_rejected(self):
        for view in self.views():
            interaction = self.interaction()
            interaction.guild = SimpleNamespace(id=999)
            self.assertFalse(await view.interaction_check(interaction))



class SettingsBatchTests(unittest.TestCase):
    setUp = fixtures.OnboardingTests.setUp

    def test_missing_empty_duplicate_and_parameterized_keys(self):
        for key, value in [('empty',''), ('zero','0'), ("quoted' key",'literal')]:
            db.set_setting(key, value)
        self.assertEqual(db.get_settings(['empty','zero',"quoted' key",'missing','zero']), {'empty':'','zero':'0',"quoted' key":'literal'})
        with patch.object(db, 'connect', side_effect=AssertionError('no DB needed')):
            self.assertEqual(db.get_settings([]), {})

    def test_large_batch_and_fresh_read(self):
        expected = {str(i):str(i * 2) for i in range(1100)}
        with db.connect() as conn:
            conn.executemany('INSERT INTO settings VALUES (?,?)', expected.items())
        self.assertEqual(db.get_settings(iter(expected)), expected)
        db.set_setting('1', 'new')
        self.assertEqual(db.get_settings(['1']), {'1':'new'})

    def test_connection_rolls_back_failed_transaction(self):
        with self.assertRaises(RuntimeError):
            with db.connect() as conn:
                conn.execute("INSERT INTO settings VALUES ('rollback-test','temporary')")
                raise RuntimeError('synthetic failure')
        self.assertIsNone(db.get_setting('rollback-test'))


class PublicUrlTests(unittest.TestCase):
    def test_shared_policy_preserves_affiliates_and_rejects_unsafe_values(self):
        from services.url_service import validate_url
        from services.managed_message_service import validate_url as compatibility
        self.assertIs(validate_url, compatibility)
        for url in ('https://example.com/path?ref=public-code#details', 'https://amzn.to/example', 'https://www.instant-gaming.com/?igr=public-code', 'https://bücher.example/'):
            with self.subTest(url=url): self.assertIsNone(validate_url(url))
        for url in ('http://example.com', 'javascript:alert(1)', 'file:///tmp/a', 'data:text/plain,x', 'https://' + 'user:password@' + 'example.com', 'https://example.com/?token=private', 'https://example.com/#api_key=private', 'https://example.com:99999/', 'https://example.com/ space', 'https://example.com/'+'a'*512):
            with self.subTest(url=url), self.assertRaises(ServerMessageError): validate_url(url)


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_discord_closes_even_if_twitch_cleanup_fails(self):
        from bot import GamerHQBot
        from discord.ext import commands
        bot = object.__new__(GamerHQBot)
        bot.health_task = None
        bot.twitch_hub = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError('synthetic cleanup failure')))
        with patch.object(commands.Bot, 'close', new_callable=AsyncMock) as close:
            with self.assertRaises(RuntimeError):
                await bot.close()
            close.assert_awaited_once()

    async def test_finished_heartbeat_failure_does_not_skip_other_cleanup(self):
        import asyncio
        from bot import GamerHQBot
        from discord.ext import commands
        bot = object.__new__(GamerHQBot)
        async def failed(): raise RuntimeError('synthetic heartbeat failure')
        bot.health_task = asyncio.create_task(failed())
        await asyncio.sleep(0)
        bot.twitch_hub = SimpleNamespace(close=AsyncMock())
        with patch.object(commands.Bot, 'close', new_callable=AsyncMock) as close:
            with self.assertRaises(RuntimeError): await bot.close()
            bot.twitch_hub.close.assert_awaited_once()
            close.assert_awaited_once()

    async def test_normal_shutdown_cancels_heartbeat_and_closes_both_clients(self):
        import asyncio
        from bot import GamerHQBot
        from discord.ext import commands
        bot = object.__new__(GamerHQBot)
        bot.health_task = asyncio.create_task(asyncio.sleep(60))
        bot.twitch_hub = SimpleNamespace(close=AsyncMock())
        with patch.object(commands.Bot, 'close', new_callable=AsyncMock) as close:
            await bot.close()
            self.assertTrue(bot.health_task.cancelled())
            bot.twitch_hub.close.assert_awaited_once()
            close.assert_awaited_once()


class GameAdminSessionTests(unittest.IsolatedAsyncioTestCase):
    setUp = RoleSessionTests.setUp
    interaction = RoleSessionTests.interaction
    def views(self):
        from cogs import games
        cog = SimpleNamespace(bot=None)
        game = dict(id=1, name='Synthetic Game', emoji='🎮', role_id=None, selectable=1, display_group='Test')
        return [games.ConfirmGameAddView(cog, game, {}, 2),
                games.DeleteGameConfirmView(cog, game, 2),
                games.ConfirmInitialSetupView(cog, 2),
                games.ConfirmOverviewView(cog, 2),
                games.ConfirmRenameView(cog, game, 'New Name', '🎮', 2)]

    async def test_revoked_or_departed_admin_cannot_reuse_role_session(self):
        for member in (SimpleNamespace(id=2, guild_permissions=discord.Permissions()), None):
            self.members[2] = member
            for view in self.views():
                interaction = self.interaction()
                await view.confirm.callback(interaction)
                self.assertIn('Administrator access', interaction.response.send_message.call_args.args[0])
                interaction.response.defer.assert_not_awaited()

    async def test_current_admin_allowed_and_other_actor_denied(self):
        from services.response_service import check_admin
        self.assertTrue(await check_admin(self.interaction()))
        for view in self.views():
            other = self.interaction(); other.user=SimpleNamespace(id=4)
            await view.confirm.callback(other)
            self.assertTrue(other.response.send_message.call_args.kwargs['ephemeral'])
            other.response.defer.assert_not_awaited()

    async def test_permission_revoked_during_defer_stops_confirmation(self):
        from cogs.games import ConfirmOverviewView
        interaction = self.interaction()
        async def revoke(**kwargs):
            self.members[2] = None
            interaction.response.is_done = lambda:True
        interaction.response.defer.side_effect = revoke
        with patch('cogs.games.rebuild_choose_games_message', new_callable=AsyncMock) as rebuild:
            await ConfirmOverviewView(SimpleNamespace(bot=None),2).confirm.callback(interaction)
            rebuild.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()

    async def test_wrong_guild_is_rejected(self):
        from services.response_service import check_admin
        interaction = self.interaction(); interaction.guild=SimpleNamespace(id=999)
        self.assertFalse(await check_admin(interaction,self.guild))
