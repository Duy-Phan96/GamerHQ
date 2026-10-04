"""Offline acceptance of staff lobby setup, stale reviews, and uncertain voice state."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import test_lobby_admin as fixtures
from cogs import lobby_admin as admin
from database import db
from services import lobby_dashboard


class LobbyAdminSetupTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.LobbyAdminTests.setUp
    actor = fixtures.LobbyAdminTests.actor
    request = fixtures.LobbyAdminTests.request

    def staff(self):
        parent = MagicMock(spec=discord.CategoryChannel)
        parent.id, parent.name, parent.position = 77, '🔒 STAFF', 0
        parent.guild = self.guild
        parent.overwrites = {self.guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        parent.overwrites_for.side_effect = lambda target: parent.overwrites.get(target, discord.PermissionOverwrite())
        self.guild.categories = [parent]
        self.guild.text_channels = []
        self.guild.fetch_channels = AsyncMock(return_value=[parent])
        self.guild.fetch_member = AsyncMock(return_value=self.moderator)
        return parent

    async def test_confirmed_setup_creates_private_channel_and_reuses_it(self):
        parent = self.staff()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.name, channel.position = 456, admin.LABEL, 0
        channel.guild, channel.category_id, channel.category = self.guild, parent.id, parent
        channel.overwrites = {}
        channel.overwrites_for.side_effect = lambda target: channel.overwrites.get(target, discord.PermissionOverwrite())
        channel.permissions_for.return_value = discord.Permissions(view_channel=True, send_messages=True, read_message_history=True)

        async def create(*args, **kwargs):
            channel.overwrites = kwargs['overwrites']
            return channel

        async def edit(**kwargs):
            channel.overwrites = kwargs['overwrites']
            return channel

        self.guild.create_text_channel = AsyncMock(side_effect=create)
        channel.edit = AsyncMock(side_effect=edit)
        message = SimpleNamespace(id=789)

        async def publish(destination, **kwargs):
            self.assertIs(destination, channel)
            self.assertFalse(destination.overwrites_for(self.guild.default_role).view_channel)
            self.assertTrue(kwargs['pin'])
            self.assertTrue(kwargs['view'].is_persistent())
            self.assertEqual(kwargs['allowed_mentions'].to_dict(), discord.AllowedMentions.none().to_dict())
            db.set_setting(kwargs['setting_key'], message.id)
            return message

        with patch('services.server_service.upsert_fixed_message', AsyncMock(side_effect=publish)) as upsert:
            request = self.request(self.moderator)
            await admin.open_management(request)
            self.guild.create_text_channel.assert_not_awaited()
            view = request.response.send_message.call_args.kwargs['view']
            await view.confirm.callback(request)
            self.guild.create_text_channel.assert_awaited_once()
            self.assertEqual(db.get_setting(admin.key(self.guild)), '456')
            self.assertEqual(db.get_setting(admin.board_key(self.guild)), '789')
            self.assertIn('ready', request.edit_original_response.call_args.kwargs['content'])

            # The second setup sees the gateway update and edits the same channel.
            self.guild.get_channel.side_effect = lambda cid: {77: parent, 456: channel}.get(cid)
            self.guild.text_channels = [channel]
            self.guild.fetch_channels.return_value = [parent, channel]
            second = self.request(self.moderator)
            await admin.open_management(second)
            second_view = second.response.send_message.call_args.kwargs['view']
            await second_view.confirm.callback(second)
            self.guild.create_text_channel.assert_awaited_once()
            channel.edit.assert_awaited_once()
            self.assertEqual(upsert.await_count, 2)
            self.assertEqual(db.get_setting(admin.board_key(self.guild)), '789')
            self.assertEqual(db.get_lfg_event(self.event['id'])['status'], 'scheduled')

    async def test_unknown_same_name_channel_blocks_provisioning(self):
        parent = self.staff()
        unknown = MagicMock(spec=discord.TextChannel)
        unknown.id, unknown.name = 888, admin.LABEL
        self.guild.fetch_channels.return_value = [parent, unknown]
        request = self.request(self.moderator)
        await admin.open_management(request)
        view = request.response.send_message.call_args.kwargs['view']
        await view.confirm.callback(request)
        self.guild.create_text_channel.assert_not_called()
        unknown.edit.assert_not_called()
        self.assertIsNone(db.get_setting(admin.key(self.guild)))

    async def test_expired_close_review_does_not_mutate_or_fetch(self):
        view = admin.CloseConfirm(self.guild.id, self.moderator.id, self.event)
        view.expires = 0
        request = self.request(self.moderator)
        await view.confirm.callback(request)
        self.assertEqual(db.get_lfg_event(self.event['id'])['status'], 'scheduled')
        self.guild.fetch_member.assert_not_called()
        request.response.defer.assert_not_awaited()

    def test_closed_event_waiting_for_voice_stays_in_inventory(self):
        with db.connect() as conn:
            conn.execute('UPDATE lfg_events SET voice_channel_id=123 WHERE id=?', (self.event['id'],))
        current = db.get_lfg_event(self.event['id'])
        admin.close_record(self.guild, self.moderator, current['id'], admin.fingerprint(current))
        text, rows, total, _ = admin.overview(self.guild)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['status'], 'cancelled')
        self.assertIn('cleanup pending', text)
        db.clear_lfg_event_voice(current['id'])
        self.assertEqual(admin.inventory(self.guild)[1], 0)

    async def test_unknown_voice_state_blocks_all_destructive_cleanup(self):
        with db.connect() as conn:
            conn.execute('UPDATE lfg_events SET voice_channel_id=123,private_channel_id=456 WHERE id=?', (self.event['id'],))
        current = db.get_lfg_event(self.event['id'])
        admin.close_record(self.guild, self.moderator, current['id'], admin.fingerprint(current))
        with db.connect() as conn:
            conn.execute('UPDATE lfg_events SET ended_at=? WHERE id=?', (int(time.time()) - 86401, current['id']))
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id, voice.members = 123, []
        self.guild.fetch_channel = AsyncMock(return_value=voice)
        with patch('cogs.lfg.delete_event_voice', AsyncMock()) as delete_voice, \
             patch('cogs.lfg.delete_private_event_channel', AsyncMock()) as delete_chat, \
             patch('cogs.lfg.delete_event_posts', AsyncMock()) as delete_posts:
            await lobby_dashboard.cleanup_ended(self.guild, event_id=current['id'])
            delete_voice.assert_not_awaited()
            delete_chat.assert_not_awaited()
            delete_posts.assert_not_awaited()
            self.guild.unavailable = True
            await lobby_dashboard.cleanup_ended(self.guild, event_id=current['id'])
            delete_voice.assert_not_awaited()
            delete_chat.assert_not_awaited()
            delete_posts.assert_not_awaited()
        self.assertEqual(db.get_lfg_event(current['id'])['private_channel_id'], 456)
