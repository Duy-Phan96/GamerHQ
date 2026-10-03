"""Administrator-only lobby dashboard: temporary DB, no Discord requests."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from database import db
from cogs import lobby_admin as admin
from services import lobby_dashboard, lobby_service


class LobbyAdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(db, 'DB_PATH', Path(self.temp.name) / 'admin.db')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        db.init_db()
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id, self.guild.owner_id, self.guild.unavailable = 1, 10, False
        self.guild.get_channel.return_value = None
        self.guild.roles = []
        self.guild.default_role = MagicMock(spec=discord.Role)
        self.guild.default_role.id = 1
        self.guild.default_role.is_default.return_value = True
        self.guild.default_role.permissions = discord.Permissions.none()
        self.guild.me = MagicMock(spec=discord.Member)
        self.guild.me.id = 999
        self.host, self.moderator, self.member = self.actor(10), self.actor(20, True), self.actor(30)
        self.event = db.create_lfg_event(guild_id=1, host_id=10, game_id=None, title='Private meetup',
            start_at=int(time.time()) + 3600, max_players=5, invite_lead_minutes=15, visibility='private', share_token='test-only-secret')
        admin._locks.clear()
        admin._signatures.clear()
        lobby_dashboard._locks.clear()

    def actor(self, user_id, administrator=False):
        member = MagicMock(spec=discord.Member)
        member.id, member.guild, member.bot = user_id, self.guild, False
        member.guild_permissions = discord.Permissions(administrator=administrator)
        return member

    def request(self, member):
        return SimpleNamespace(guild=self.guild, user=member,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
            edit_original_response=AsyncMock())

    def test_only_owner_or_administrator_can_close(self):
        self.assertTrue(admin.admin(self.guild, self.host))
        self.assertTrue(admin.admin(self.guild, self.moderator))
        self.assertFalse(admin.admin(self.guild, self.member))
        self.member.guild_permissions.manage_guild = True
        self.assertFalse(admin.admin(self.guild, self.member))
        with self.assertRaises(ValueError):
            admin.close_record(self.guild, self.member, self.event['id'], admin.fingerprint(self.event))
        event = admin.close_record(self.guild, self.moderator, self.event['id'], admin.fingerprint(self.event))
        self.assertEqual(event['status'], 'cancelled')
        self.assertEqual(event['share_enabled'], 0)
        audit = json.loads(db.get_setting(f"lobby_admin_close:1:{event['id']}"))
        self.assertEqual(audit['actor_id'], 20)
        self.assertNotIn('test-only-secret', json.dumps(audit))
        self.assertEqual(event['host_id'], 10)
        self.assertGreater(event['ended_at'], 0)
        with self.assertRaises(ValueError):
            admin.close_record(self.guild, self.moderator, event['id'], admin.fingerprint(self.event))

    def test_ordinary_host_management_stays_host_only(self):
        with self.assertRaises(ValueError):
            lobby_service.end(self.event['id'], 1, 20, 'cancelled', administrator=True)

    def test_stale_preview_and_other_guild_are_rejected(self):
        expected = admin.fingerprint(self.event)
        with db.connect() as conn:
            conn.execute('UPDATE lfg_events SET title=? WHERE id=?', ('Changed', self.event['id']))
        with self.assertRaises(ValueError):
            admin.close_record(self.guild, self.moderator, self.event['id'], expected)
        other = MagicMock(spec=discord.Guild)
        other.id, other.owner_id = 2, 20
        with self.assertRaises(ValueError):
            admin.close_record(other, self.moderator, self.event['id'], expected)
        self.assertEqual(db.get_lfg_event(self.event['id'])['status'], 'scheduled')

    def test_inventory_is_private_scoped_paginated_and_secret_free(self):
        for index in range(10):
            db.create_lfg_event(guild_id=1, host_id=10, title=f'Event {index}', game_id=None,
                start_at=int(time.time()) + 7200, max_players=5, invite_lead_minutes=15)
        db.create_lfg_event(guild_id=2, host_id=50, title='Other guild', game_id=None,
            start_at=int(time.time()) + 7200, max_players=5, invite_lead_minutes=15)
        text, rows, total, page = admin.overview(self.guild)
        self.assertEqual((len(rows), total, page), (6, 11, 0))
        self.assertNotIn('test-only-secret', text)
        self.assertNotIn('Other guild', text)
        _, last, _, last_page = admin.overview(self.guild, 999)
        self.assertEqual((len(last), last_page), (5, 1))
        self.guild.fetch_members.assert_not_called()
        self.guild.fetch_channels.assert_not_called()

    async def test_entry_and_callbacks_recheck_permissions(self):
        self.assertTrue(admin.AdminEntry().is_persistent())
        denied = self.request(self.member)
        await admin.open_overview(denied)
        denied.response.send_message.assert_awaited_once()
        denied.response.defer.assert_not_awaited()
        view = admin.CloseConfirm(1, 20, self.event)
        request = self.request(self.moderator)
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        await view.confirm.callback(request)
        self.assertEqual(db.get_lfg_event(self.event['id'])['status'], 'scheduled')
        self.guild.create_text_channel.assert_not_called()

    async def test_confirmation_closes_only_selected_event_and_is_single_use(self):
        view = admin.CloseConfirm(1, 20, self.event)
        request = self.request(self.moderator)
        self.guild.fetch_member = AsyncMock(return_value=self.moderator)
        with patch('cogs.lfg.refresh_event_posts', AsyncMock()), \
             patch.object(admin, 'cleanup_ended', AsyncMock()) as cleanup, \
             patch.object(admin, 'refresh_board', AsyncMock()), \
             patch('services.server_log_service.emit', AsyncMock()):
            await view.confirm.callback(request)
            await view.confirm.callback(request)
        cleanup.assert_awaited_once_with(self.guild, event_id=self.event['id'])
        self.assertEqual(db.get_lfg_event(self.event['id'])['status'], 'cancelled')
        self.guild.fetch_member.assert_awaited_once()

    async def test_busy_voice_keeps_chat_and_voice_until_empty(self):
        with db.connect() as conn:
            conn.execute('UPDATE lfg_events SET voice_channel_id=123,private_channel_id=456 WHERE id=?', (self.event['id'],))
        event = db.get_lfg_event(self.event['id'])
        admin.close_record(self.guild, self.moderator, event['id'], admin.fingerprint(event))
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id, voice.members = 123, [self.host]
        self.guild.get_channel.side_effect = lambda cid: voice if cid == 123 else None
        with patch('cogs.lfg.delete_event_posts', AsyncMock(return_value=True)) as posts, \
             patch('cogs.lfg.delete_event_voice', AsyncMock(return_value=True)) as delete_voice, \
             patch('cogs.lfg.delete_private_event_channel', AsyncMock(return_value=True)) as delete_chat:
            await lobby_dashboard.cleanup_ended(self.guild, event_id=event['id'])
            delete_voice.assert_not_awaited()
            delete_chat.assert_not_awaited()
            posts.assert_not_awaited()
            voice.members = []
            await lobby_dashboard.cleanup_ended(self.guild, event_id=event['id'])
            delete_voice.assert_awaited_once()
            delete_chat.assert_awaited_once()

    def test_channel_denies_normal_staff_and_members(self):
        staff_role = MagicMock(spec=discord.Role)
        staff_role.id = 40
        staff_role.is_default.return_value = False
        staff_role.permissions = discord.Permissions(manage_messages=True)
        self.guild.roles = [staff_role]
        channel = SimpleNamespace(overwrites={staff_role: discord.PermissionOverwrite(view_channel=True)})
        rights = admin.private_rights(self.guild, channel)
        self.assertFalse(rights[self.guild.default_role].view_channel)
        self.assertFalse(rights[staff_role].view_channel)
        self.assertTrue(rights[self.guild.me].view_channel)

    async def test_background_refresh_never_creates_channels_or_unmapped_posts(self):
        self.assertFalse(await admin.refresh_board(self.guild))
        self.guild.create_text_channel.assert_not_called()
        self.guild.fetch_channels.assert_not_called()

    async def test_background_refresh_skips_unchanged_and_rejects_foreign_message(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 456
        text = admin.overview(self.guild)[0]
        message = SimpleNamespace(id=789, author=self.guild.me, content=text, edit=AsyncMock())
        channel.fetch_message = AsyncMock(return_value=message)
        db.set_setting(admin.board_key(self.guild), 789)
        with patch.object(admin, 'destination', return_value=channel):
            await admin.refresh_board(self.guild)
            await admin.refresh_board(self.guild)
            channel.fetch_message.assert_awaited_once()
            message.edit.assert_not_awaited()
            admin._signatures.clear()
            message.author = self.member
            self.assertFalse(await admin.refresh_board(self.guild))
            message.edit.assert_not_awaited()

    async def test_setup_requires_explicit_confirmation_and_fresh_permissions(self):
        parent = MagicMock(spec=discord.CategoryChannel)
        parent.id, parent.name = 77, '🔒 STAFF'
        parent.overwrites_for.return_value = discord.PermissionOverwrite(view_channel=False)
        self.guild.categories = [parent]
        request = self.request(self.moderator)
        await admin.open_management(request)
        self.guild.create_text_channel.assert_not_called()
        view = request.response.send_message.call_args.kwargs['view']
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        await view.confirm.callback(request)
        self.guild.create_text_channel.assert_not_called()

    async def test_cancel_does_not_change_event(self):
        request = self.request(self.moderator)
        view = admin.CloseConfirm(1, 20, self.event)
        await view.cancel.callback(request)
        self.assertEqual(db.get_lfg_event(self.event['id'])['status'], 'scheduled')
        self.assertIsNone(db.get_setting(f"lobby_admin_close:1:{self.event['id']}"))
