import asyncio
import sqlite3
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import config
from database import db, twitch
from services import streamer_hub_service as hub
from services.twitch_service import TwitchHub, TwitchAPI, TwitchError, notification, WS_URL
from cogs.twitch_hub import HubView, ConfirmConnection, DisconnectView, open_hub, wait_for_device
from cogs.roles import ChooseRolesHubView
from tests.test_onboarding import FakeGuild, FakeMessage

ACCOUNT = {'id': '123', 'login': 'test_streamer', 'display_name': 'Test Streamer'}
TOKENS = {'access_token': 'test-access', 'refresh_token': 'test-refresh', 'expires_in': 14400}
STREAM = {'id': 'session-1', 'user_id': '123', 'started_at': '2026-09-24T10:00:00Z', 'title': 'A game', 'game_name': 'A category'}


class Defaults(unittest.TestCase):
    def test_beta_defaults_hidden_and_startup_requires_client_only_when_enabled(self):
        self.assertFalse(config.STREAMER_HUB_ENABLED)
        self.assertFalse(config.STREAMER_ROLE_SELECTION_ENABLED)
        with patch.object(config, 'TOKEN', 'test'), patch.object(config, 'GUILD_ID', 1):
            config.validate_startup()
            with patch.object(config, 'STREAMER_HUB_ENABLED', True):
                with self.assertRaises(config.ConfigurationError): config.validate_startup()


class TwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for target, value in [('database.db.DB_PATH', Path(directory.name)/'test.db'),
                              ('config.STREAMER_HUB_ENABLED', True), ('config.GUILD_ID', 1), ('config.TWITCH_CLIENT_ID', 'test-client')]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        db.init_db()
        self.guild = FakeGuild()
        self.guild.owner_id = 888
        self.category = self.guild.add_category('🎥 STREAMERS')
        self.target = self.guild.add_channel('🔴・stream-updates', self.category)
        db.set_setting('managed_channel:1:stream-updates', self.target.id)
        self.role = self.guild.custom
        self.role.name = '🎥 Streamer'
        db.set_setting('managed_role:1:streamer', self.role.id)
        self.member = MagicMock(spec=discord.Member)
        self.member.id, self.member.guild, self.member.roles = 99, self.guild, [self.role]
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        self.bot = SimpleNamespace(get_guild=lambda gid: self.guild if gid == 1 else None)
        self.manager = TwitchHub(self.bot)
        self.bot.twitch_hub = self.manager
        self.manager.api = SimpleNamespace(stream=AsyncMock(return_value=STREAM), revoke=AsyncMock(),
            account=AsyncMock(return_value=ACCOUNT), refresh=AsyncMock(return_value=TOKENS), subscribe=AsyncMock())
        self.addAsyncCleanup(self.manager.close)

    def save(self):
        twitch.save(1, 99, ACCOUNT, TOKENS)
        return twitch.connection(1, 99)

    def interaction(self, uid=99):
        return SimpleNamespace(guild=self.guild, user=SimpleNamespace(id=uid), client=self.bot,
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
            edit_original_response=AsyncMock())

    async def test_authorization_flag_role_and_guild(self):
        self.assertTrue(await hub.authorize(self.guild, 99))
        self.member.roles = []
        self.assertFalse(await hub.authorize(self.guild, 99))
        self.member.roles = [self.guild.mod]
        self.assertTrue(await hub.authorize(self.guild, 99))
        with patch.object(config, 'STREAMER_HUB_ENABLED', False):
            self.assertFalse(await hub.authorize(self.guild, 99))
        self.assertFalse(await hub.authorize(None, 99))

    async def test_hidden_ui_and_server_side_role_selection(self):
        self.assertFalse(any(b.label == 'Streamer' for b in ChooseRolesHubView().children))
        with self.assertRaises(ValueError): await hub.toggle_role(self.guild, 99)
        with patch.object(config, 'STREAMER_ROLE_SELECTION_ENABLED', True):
            self.assertTrue(any(b.label == 'Streamer' for b in ChooseRolesHubView().children))
            self.assertFalse(await hub.toggle_role(self.guild, 99))
            self.member.remove_roles.assert_awaited_once_with(self.role, reason='GamerHQ explicit Streamer opt-out')

    async def test_legacy_area_tools_remain_staff_only_when_beta_disabled(self):
        from cogs.streamer import Streamer
        cog = Streamer(self.bot)
        interaction = self.interaction()
        interaction.command = SimpleNamespace(name='area')
        with patch.object(config, 'STREAMER_HUB_ENABLED', False):
            self.assertFalse(await cog.interaction_check(interaction))
            self.member.roles = [self.guild.mod]
            self.assertTrue(await cog.interaction_check(interaction))

    async def test_disabled_connect_is_denied_even_stale_button(self):
        interaction = self.interaction()
        with patch.object(config, 'STREAMER_HUB_ENABLED', False):
            await HubView().children[0].callback(interaction)
        interaction.response.send_message.assert_awaited_once_with(hub.DENIED, ephemeral=True)

    def test_persistence_restart_and_unique_accounts(self):
        self.save()
        db.init_db()
        self.assertEqual(twitch.connection(1, 99)['twitch_user_id'], '123')
        with self.assertRaises(sqlite3.IntegrityError): twitch.save(1, 100, ACCOUNT, TOKENS)
        with self.assertRaises(sqlite3.IntegrityError): twitch.save(1, 99, {**ACCOUNT, 'id': '456'}, TOKENS)

    async def test_connect_confirmation_bound_to_discord_user_and_nonce(self):
        self.manager.launch = MagicMock()
        self.manager.attempts[(1, 99)] = 'nonce'
        view = ConfirmConnection(self.manager, (1, 99), 'nonce', ACCOUNT, TOKENS)
        await view.children[0].callback(self.interaction(100))
        self.assertIsNone(twitch.connection(1, 99))
        await view.children[0].callback(self.interaction())
        self.assertEqual(twitch.connection(1, 99)['twitch_login'], ACCOUNT['login'])
        await view.children[0].callback(self.interaction())
        self.manager.launch.assert_called_once()

    async def test_stale_and_expired_confirmations_cannot_connect(self):
        for nonce, expiry in [('old', False), ('new', True)]:
            self.manager.attempts[(1, 99)] = 'new'
            view = ConfirmConnection(self.manager, (1, 99), nonce, ACCOUNT, TOKENS)
            if expiry: view.deadline = 0
            await view.children[0].callback(self.interaction())
        self.assertIsNone(twitch.connection(1, 99))

    async def test_device_flow_pending_then_identity_confirmation(self):
        self.manager.attempts[(1, 99)] = 'nonce'
        self.manager.api.exchange = AsyncMock(side_effect=[TwitchError(400, 'authorization_pending'), TOKENS])
        interaction = self.interaction()
        with patch('cogs.twitch_hub.asyncio.sleep', new=AsyncMock()):
            await wait_for_device(interaction, self.manager, (1, 99), 'nonce', {'interval':5, 'expires_in':60, 'device_code':'test-device'})
        self.assertIsNone(twitch.connection(1, 99))  # Explicit Discord confirmation still required.
        view = interaction.edit_original_response.call_args.kwargs['view']
        self.assertIsInstance(view, ConfirmConnection)
        self.assertEqual(self.manager.api.exchange.await_count, 2)

    async def test_connected_state_and_disconnect_confirmation(self):
        self.save()
        interaction = self.interaction()
        await open_hub(interaction)
        self.assertIn('Connected as Test Streamer', interaction.edit_original_response.call_args.kwargs['content'])
        view = DisconnectView(99)
        await view.disconnect(interaction)
        self.assertIsNotNone(twitch.connection(1, 99))
        confirmation = interaction.response.edit_message.call_args.kwargs['view']
        await confirmation.disconnect(self.interaction(100))
        self.assertIsNotNone(twitch.connection(1, 99))
        await confirmation.disconnect(self.interaction())
        self.assertIsNone(twitch.connection(1, 99))
        self.member.remove_roles.assert_not_called()

    async def test_online_once_concurrent_restart_reconnect_and_disconnect(self):
        row = self.save()
        await asyncio.gather(self.manager.online(row), self.manager.online(row))
        other = TwitchHub(self.bot); other.api = self.manager.api
        await other.online(row)
        self.assertEqual(self.target.sends, 1)
        twitch.disconnect(1, 99)
        row = self.save()
        await other.online(row)
        self.assertEqual(self.target.sends, 1)
        message = next(iter(self.target.messages.values()))
        self.assertIn('Test Streamer', message.content)
        self.assertIn('A game', message.content)
        self.assertIn('A category', message.content)
        self.assertEqual(message.view.children[0].url, 'https://www.twitch.tv/test_streamer')

    async def test_new_session_posts_but_stale_session_does_not(self):
        row = self.save()
        await self.manager.online(row, 'stale-id')
        self.assertEqual(self.target.sends, 0)
        await self.manager.online(row)
        self.manager.api.stream.return_value = {**STREAM, 'id': 'session-2'}
        await self.manager.online(row)
        self.assertEqual(self.target.sends, 2)

    async def test_no_post_disabled_revoked_disconnected_or_unmapped(self):
        row = self.save()
        with patch.object(config, 'STREAMER_HUB_ENABLED', False): await self.manager.online(row)
        self.member.roles = []
        await self.manager.online(row)
        self.member.roles = [self.role]
        db.set_setting('managed_channel:1:stream-updates', '')
        await self.manager.online(row)
        twitch.disconnect(1, 99)
        await self.manager.online(row)
        self.assertEqual(self.target.sends, 0)

    async def test_uncertain_delivery_never_retried(self):
        row = self.save()
        self.target.send = AsyncMock(side_effect=TimeoutError())
        await self.manager.online(row)
        await self.manager.online(row)
        self.target.send.assert_awaited_once()
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM twitch_live_deliveries').fetchone()[0], 'uncertain')

    def test_optional_fields_and_mentions(self):
        content = notification({**ACCOUNT, 'twitch_login': ACCOUNT['login'], 'twitch_display_name':'@everyone'}, {})
        self.assertNotIn('None', content['content'])
        self.assertNotIn('🎮', content['content'])
        self.assertFalse(content['allowed_mentions'].everyone)
        with self.assertRaises(ValueError): notification({'twitch_login':'https://evil.invalid', 'twitch_display_name':'Bad'}, {})

    async def test_offline_private_state_only_and_stale_events_ignored(self):
        row = self.save()
        await self.manager.online(row)
        packet = {'metadata': {'message_timestamp':'2026-09-24T09:00:00Z'}, 'payload': {'subscription':{'type':'stream.offline'}, 'event':{'broadcaster_user_id':'123'}}}
        await self.manager.event(row, packet)
        self.assertEqual(twitch.connection(1, 99)['is_live'], 1)
        packet['metadata']['message_timestamp'] = '2026-09-24T11:00:00Z'
        await self.manager.event(row, packet)
        self.assertEqual(twitch.connection(1, 99)['is_live'], 0)
        self.assertEqual(self.target.sends, 1)
        self.assertFalse(next(iter(self.target.messages.values())).deleted)

    async def test_token_rotation_persisted_and_validated(self):
        row = self.save()
        row['expires_at'] = 0
        self.manager.api.refresh.return_value = {**TOKENS, 'access_token':'test-rotated', 'refresh_token':'test-next'}
        result = await self.manager.valid_token(row)
        self.assertEqual(result['refresh_token'], 'test-next')
        self.manager.api.account.assert_awaited_once_with('test-rotated', '123')

    async def test_api_validates_client_and_account_identity(self):
        api = TwitchAPI()
        api.request = AsyncMock(return_value={'client_id':'wrong-client', 'user_id':'123'})
        with self.assertRaises(TwitchError): await api.account('test-access')
        api.request = AsyncMock(side_effect=[{'client_id':'test-client','user_id':'123'}, {'data':[ACCOUNT]}])
        self.assertEqual(await api.account('test-access'), ACCOUNT)

    async def test_device_oauth_has_no_unused_secret_redirect_or_scopes(self):
        api = TwitchAPI(); api.request = AsyncMock()
        await api.device()
        self.assertEqual(api.request.call_args.kwargs['data'], {'client_id':'test-client', 'scopes':''})
        await api.exchange('test-device')
        self.assertEqual(api.request.call_args.kwargs['data']['grant_type'], 'urn:ietf:params:oauth:grant-type:device_code')

    async def test_restart_launches_only_enabled_persisted_connections(self):
        self.save()
        self.manager.launch = MagicMock()
        await self.manager.start()
        await self.manager.start()
        self.manager.launch.assert_called_once()
        self.assertTrue(self.manager.initialized)

    async def test_websocket_reconnect_transfers_without_duplicate_subscription(self):
        row = self.save()
        initial = MagicMock(); initial.close = AsyncMock()
        replacement = MagicMock(); replacement.close = AsyncMock()
        initial.receive_json = AsyncMock(side_effect=[{'payload':{'session':{'id':'s1','keepalive_timeout_seconds':10}}},
            {'metadata':{'message_type':'session_reconnect'}, 'payload':{'session':{'reconnect_url':WS_URL+'?reconnect=test'}}}])
        replacement.receive_json = AsyncMock(side_effect=[{'metadata':{'message_type':'session_welcome'}, 'payload':{'session':{'id':'s2'}}}, RuntimeError('end test')])
        session = MagicMock(); session.ws_connect = AsyncMock(side_effect=[initial,replacement])
        context = AsyncMock(); context.__aenter__.return_value = session
        with patch('services.twitch_service.aiohttp.ClientSession', return_value=context):
            with self.assertRaises(RuntimeError): await self.manager.socket(row)
        self.manager.api.subscribe.assert_awaited_once_with(row, 's1')
        self.assertEqual(self.target.sends, 1)
        replacement.close.assert_awaited_once()

    async def test_recovery_reconciles_offline_without_posting(self):
        row = self.save()
        twitch.live(row, STREAM)
        self.manager.api.stream.return_value = None
        await self.manager.online(row)
        self.assertEqual(twitch.connection(1, 99)['is_live'], 0)
        self.assertEqual(self.target.sends, 0)

    async def test_revoked_authorization_stops_worker(self):
        self.save()
        self.manager.api.account.side_effect = TwitchError(401)
        self.manager.api.refresh.side_effect = TwitchError(400)
        await self.manager.watch((1, 99))
        self.assertFalse(twitch.connection(1, 99)['notifications_enabled'])
        self.assertEqual(self.target.sends, 0)

    async def test_legacy_follower_callback_disabled(self):
        from cogs.streamer import StreamerFollowSelect
        cog = SimpleNamespace(ensure_follower_role=AsyncMock())
        select = StreamerFollowSelect(cog, [], self.guild)
        await select.callback(self.interaction())
        cog.ensure_follower_role.assert_not_called()

    async def test_api_subscriptions_scoped_and_persisted(self):
        row = self.save()
        api = TwitchAPI()
        api.request = AsyncMock(side_effect=[{'data':[{'id':'online-sub'}]}, {'data':[{'id':'offline-sub'}]}])
        await api.subscribe(row, 'socket-id')
        bodies = [call.kwargs['body'] for call in api.request.call_args_list]
        self.assertEqual([b['type'] for b in bodies], ['stream.online', 'stream.offline'])
        self.assertTrue(all(b['condition'] == {'broadcaster_user_id':'123'} and b['transport'] == {'method':'websocket','session_id':'socket-id'} for b in bodies))
        self.assertIn('online-sub', twitch.connection(1, 99)['subscription_ids'])

    async def test_enabled_setup_idempotent_ids_and_pins_preserved(self):
        await hub.sync(self.guild)
        ids = [c.id for c in self.guild.text_channels]
        mid = db.get_setting('streamer_guide_message_id')
        await hub.sync(self.guild)
        self.assertEqual(ids, [c.id for c in self.guild.text_channels])
        self.assertEqual(mid, db.get_setting('streamer_guide_message_id'))
        guide = hub.channel(self.guild, 'streamer-guide')
        self.assertIn(self.target.mention, next(iter(guide.messages.values())).content)
        self.assertEqual(guide.sends, 1)
        self.assertFalse(guide.overwrites_for(self.guild.default_role).view_channel)
        self.assertTrue(guide.overwrites_for(self.role).view_channel)
        self.assertTrue(self.target.overwrites_for(self.guild.default_role).view_channel)
        self.assertFalse(self.target.overwrites_for(self.guild.default_role).send_messages)

    async def test_existing_command_channel_mapping_reused_without_message(self):
        command = self.guild.add_channel('📘・streamer-commands', self.category)
        db.set_setting('server_streamer_commands_channel_id', command.id)
        await hub.sync(self.guild)
        self.assertEqual(hub.channel(self.guild, 'streamer-commands').id, command.id)
        self.assertEqual(sum('streamer-commands' in c.name for c in self.guild.text_channels), 1)

    async def test_disabled_hides_managed_legacy_without_deleting_data(self):
        guide = self.guild.add_channel('📖・streamer-guide', self.category)
        choose = self.guild.add_channel('🎬・choose-streamers', self.category)
        for channel, key, text in [(guide,'streamer_guide_message_id','# 🎥 GamerHQ Streamers'), (choose,'streamer_choose_message_id','# 🎬 Choose Streamers')]:
            msg = FakeMessage(channel, channel.id + 100, text, pinned=True)
            channel.messages[msg.id] = msg
            db.set_setting(key, msg.id)
            channel.overwrites[self.role] = discord.PermissionOverwrite(view_channel=True)
        with patch.object(config, 'STREAMER_HUB_ENABLED', False):
            await hub.sync(self.guild)
        self.assertFalse(choose.overwrites_for(self.role).view_channel)
        self.assertTrue(choose.overwrites_for(self.guild.mod).view_channel)
        self.assertIsNone(next(iter(guide.messages.values())).view)
        self.assertEqual(len(choose.messages), 1)
        self.assertEqual(len(self.guild.text_channels), 3)

    async def test_unowned_channel_not_adopted_or_duplicated(self):
        self.guild.add_channel('📖・streamer-guide', self.category)
        from services.server_service import ServerMessageError
        with self.assertRaises(ServerMessageError): await hub.sync(self.guild)
        self.assertEqual(len(self.guild.text_channels), 2)

    def test_disabled_health_info_and_enabled_health_read_only(self):
        with patch.object(config, 'STREAMER_HUB_ENABLED', False):
            self.assertEqual(hub.health(self.guild, self.bot)[1], 'INFO')
        with db.connect() as conn: before = list(conn.iterdump())
        self.manager.initialized = True
        self.assertEqual(hub.health(self.guild, self.bot)[1], 'PASS')
        with db.connect() as conn: self.assertEqual(before, list(conn.iterdump()))
