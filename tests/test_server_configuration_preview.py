"""The private preview uses live inventory and never applies a setup action."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from database import db
from services import game_service, server_configuration_preview as configuration
from services.server_service import ServerMessageError
from tests import test_onboarding as fixtures


class DiscordTextChannel(fixtures.FakeChannel):
    __class__ = discord.TextChannel


class ConfigurationPreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.OnboardingTests.setUp(self)
        self.owner = SimpleNamespace(id=42, guild_permissions=discord.Permissions(administrator=True))
        self.guild.owner_id = self.owner.id
        self.guild.members = [self.owner]
        self.guild.fetch_roles = AsyncMock(return_value=self.guild.roles)
        for role in self.guild.roles:
            role.guild = self.guild
        configured_guild = patch.object(config, 'GUILD_ID', self.guild.id)
        configured_guild.start()
        self.addCleanup(configured_guild.stop)

    async def test_seed_catalog_preview_is_stable_and_read_only(self):
        db.seed_catalog()
        before = db.get_all_games()
        original_db = db.DB_PATH.read_bytes()
        summary, rows = await configuration.preview(self.guild, self.owner, self.bot)
        self.assertEqual(await configuration.preview(self.guild, self.owner, self.bot), (summary, rows))
        self.assertTrue(any('0 active, 0 visible' in line for line in summary))
        self.assertTrue(any('38 matches, 32 proposed additions' in line for line in summary))
        self.assertIn(('category', 'events', 'Create', 'No matching resource'), rows)
        self.assertTrue(any(kind == 'onboarding' for kind, _, _, _ in rows))
        self.assertTrue(any(kind == 'message' for kind, _, _, _ in rows))
        self.assertEqual(before, db.get_all_games())
        self.assertEqual(original_db, db.DB_PATH.read_bytes())
        self.assertIsNone(db.get_setting('managed_category:1:events'))
        self.assertFalse(any(c.edits or c.sends for c in self.guild.text_channels))

    async def test_unlinked_message_outcomes_use_public_history_and_publish_only_metadata(self):
        from cogs.server import future_community_copies
        from services import managed_message_service as managed
        copies = future_community_copies()
        private = self.guild.add_channel('private destination', self.staff)
        private.overwrites[self.guild.default_role] = discord.PermissionOverwrite(read_message_history=False)
        private.history = private.pins = AsyncMock(side_effect=AssertionError('private message read'))
        private.fetch_message = AsyncMock(side_effect=AssertionError('private message read'))
        giveaways = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        fixtures.OnboardingTests.add_message(self, giveaways, copies['giveaways'], pinned=True)
        empty = self.guild.add_channel('test-empty', self.community)
        denied = self.guild.add_channel('test-denied', self.community)
        denied.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
        denied.history = denied.pins = AsyncMock(side_effect=AssertionError('unreadable history'))
        unknown = self.guild.add_channel('test-unknown', self.community)
        fixtures.OnboardingTests.add_message(self, unknown, 'content sentinel', pinned=True)
        boards = {
            'server_future_giveaways_message_id': (giveaways, copies['giveaways']),
            'test-empty': (empty, '# Public default'),
            'test-unknown': (unknown, '# Another default'),
            'test-private': (private, '# Private default'),
            'test-denied': (denied, '# Default'),
            'test-no-destination': (None, '# Default'),
        }
        with patch.object(managed, 'canonical_boards', return_value=boards):
            summary, rows = await configuration.preview(self.guild, self.owner, self.bot)
        outcomes = {name: (action, detail) for kind, name, action, detail in rows if kind == 'message'}
        self.assertEqual(outcomes['server_future_giveaways_message_id'][0], 'Reuse')
        self.assertEqual(outcomes['test-empty'][0], 'Create')
        for key in ('test-unknown', 'test-private', 'test-denied', 'test-no-destination'):
            self.assertEqual(outcomes[key][0], 'Conflict')
        self.assertNotIn('content sentinel', str((summary, rows)))
        self.assertNotIn('Private default', str((summary, rows)))
        private.history.assert_not_called()
        private.fetch_message.assert_not_awaited()
        denied.history.assert_not_called()

    async def test_linked_resources_legacy_chat_and_skill_state_are_preserved(self):
        gaming = self.guild.add_category('GAMING')
        event_area = self.guild.add_category('EVENT TESTING')
        chat = DiscordTextChannel(self.guild, 'legacy game chat', gaming, 8001)
        self.guild.text_channels.append(chat)
        event_chat = self.guild.add_channel('community-events', event_area)
        shared_chat = self.guild.add_channel('💬・gaming-chat', gaming)
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id, voice.name, voice.guild = 8000, '🔊 Chill Lounge', self.guild
        voice.category, voice.category_id = self.voice, self.voice.id
        voice.overwrites = {}
        self.guild.fetch_channels = AsyncMock(return_value=[*self.guild.channels, voice])
        db.set_setting('managed_category:1:gaming', gaming.id)
        db.set_setting('managed_channel:1:gaming-chat', shared_chat.id)
        db.set_setting('managed_category:1:voice-channels', self.voice.id)
        db.set_setting('managed_channel:1:chill-lounge', voice.id)
        db.set_setting('skill_resources:1:event-manager', json.dumps({'categoryId': event_area.id}))
        db.set_setting('managed_channel:999:community-events', event_chat.id)
        db.upsert_custom_game('Existing Example', '🎮', 'Andere')
        gid = db.get_game_by_name('Existing Example')['id']
        with db.connect() as conn:
            conn.execute('INSERT INTO game_legacy_hints VALUES (?, ?)', (gid, json.dumps({'chat_channel_id': chat.id})))
            conn.execute('INSERT INTO skill_guild_state VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (1, 'event-manager', 1, 1, 1, '0.1.2', 0))
            conn.execute('INSERT INTO skill_managed_resources VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (1, 'event-manager', 'existing', 'area', json.dumps({'channelId': event_chat.id}), None, 0))
        before = db.get_game_by_id(gid)
        summary, rows = await configuration.preview(self.guild, self.owner, self.bot)
        outcomes = {(kind, name): action for kind, name, action, _ in rows}
        self.assertEqual(outcomes['category', 'games'], 'Reuse')
        self.assertEqual(outcomes['text', 'gaming-chat'], 'Reuse')
        self.assertEqual(outcomes['voice', 'chill-lounge'], 'Reuse')
        self.assertEqual(outcomes['text', 'community-events'], 'Conflict')
        self.assertEqual(outcomes['skill', 'event-manager.text-area'], 'Reuse')
        self.assertTrue(any('1 games with legacy hints' in line and '1 verified chats' in line for line in summary), summary)
        self.assertEqual(db.get_game_by_id(gid), before)
        self.assertIsNone(db.get_setting('managed_channel:1:community-events'))
        self.assertFalse(event_chat.edits or event_chat.sends)
        voice.edit.assert_not_called()

    async def test_choose_games_uses_only_the_current_managed_destination_as_fallback(self):
        board = next(channel for channel in self.guild.text_channels if channel.name == 'choose-your-games')
        db.set_setting('managed_channel:999:choose-your-games', 98765)
        self.assertEqual(game_service.choose_games_channel_id(self.guild.id), 0)
        db.set_setting('managed_channel:1:choose-your-games', board.id)
        self.assertEqual(game_service.choose_games_channel_id(self.guild.id), board.id)
        with patch.object(game_service, 'CHOOSE_GAMES_CHANNEL_ID', 12345):
            self.assertEqual(game_service.choose_games_channel_id(self.guild.id), 12345)
        other = SimpleNamespace(id=999)
        with patch.object(board, 'guild', other):
            bot = SimpleNamespace(get_channel=lambda _: board)
            with self.assertRaises(game_service.GameStructureError):
                await game_service.refresh_choose_games_message(bot, view=object(), intro_view=object())
        self.assertFalse(board.sends)

    async def test_only_configured_guild_owner_can_start_inventory(self):
        outsider = SimpleNamespace(id=43, guild_permissions=discord.Permissions(administrator=True))
        self.guild.members.append(outsider)
        with self.assertRaises(ServerMessageError):
            await configuration.preview(self.guild, outsider, self.bot)
        with patch.object(config, 'GUILD_ID', 999), self.assertRaises(ServerMessageError):
            await configuration.preview(self.guild, self.owner, self.bot)
        self.guild.fetch_roles.assert_not_awaited()

    async def test_discord_failure_does_not_report_resources_as_missing(self):
        self.guild.fetch_roles.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            await configuration.preview(self.guild, self.owner, self.bot)
