"""Music-role and confirmed-area cleanup tests; temporary DB, no live Discord."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from database import db
from services import music_bot_service as music, game_area_cleanup as cleanup, game_area_safety as safety, game_service
from cogs import lfg, voice
from cogs.game_area_cleanup import CleanupConfirm, CleanupPreview


class MusicCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        patcher = patch.object(db, 'DB_PATH', Path(temp.name) / 'test.db')
        patcher.start(); self.addCleanup(patcher.stop); db.init_db()
        safety._locks.clear(); safety.cleaning.clear()
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = 1; self.guild.owner_id = 10
        self.guild.roles = []; self.guild.channels = []; self.guild.categories = []
        self.everyone = self.role(1, '@everyone', default=True)
        self.game_role = self.role(2, 'Test Game')
        self.music = self.role(3, 'Music Bots')
        self.guild.default_role = self.everyone
        self.guild.me = MagicMock(spec=discord.Member); self.guild.me.id = 99
        self.guild.get_role.side_effect = lambda rid: next((r for r in self.guild.roles if r.id == rid), None)
        self.guild.get_channel.side_effect = lambda cid: next((c for c in self.guild.channels if c.id == cid), None)
        self.guild.fetch_channels = AsyncMock(side_effect=lambda: list(self.guild.channels))
        self.actor = MagicMock(spec=discord.Member); self.actor.id = 10; self.actor.guild_permissions = discord.Permissions()
        self.game = db.upsert_custom_game('Test Game', '🎮', 'Test')
        self.category = self.channel(100, '🎮 TEST GAME', discord.CategoryChannel)
        self.category.overwrites = {self.everyone: discord.PermissionOverwrite(view_channel=False), self.game_role: discord.PermissionOverwrite(view_channel=True)}
        self.chat = self.channel(101, '💬・chat', discord.TextChannel, self.category)
        self.generator = self.channel(102, '➕・create-voice', discord.VoiceChannel, self.category)
        self.link_game()

    def role(self, rid, name, default=False):
        role = MagicMock(spec=discord.Role)
        role.id, role.name, role.managed = rid, name, False
        role.permissions = discord.Permissions()
        role.is_default.return_value = default
        self.guild.roles.append(role)
        return role

    def channel(self, cid, name, kind, category=None):
        channel = MagicMock(spec=kind)
        channel.id, channel.name, channel.guild = cid, name, self.guild
        channel.category, channel.category_id = category, category.id if category else None
        channel.last_message_id = None
        channel.overwrites = dict(category.overwrites) if category else {}
        channel.members = []
        channel.channels = []
        channel.overwrites_for.side_effect = lambda role: discord.PermissionOverwrite.from_pair(*channel.overwrites.get(role, discord.PermissionOverwrite()).pair())
        async def permissions(role, *, overwrite, **kwargs): channel.overwrites[role] = overwrite
        channel.set_permissions = AsyncMock(side_effect=permissions)
        async def delete(**kwargs):
            self.guild.channels.remove(channel)
            if category: category.channels.remove(channel)
            if channel in self.guild.categories: self.guild.categories.remove(channel)
        channel.delete = AsyncMock(side_effect=delete)
        self.guild.channels.append(channel)
        if category: category.channels.append(channel)
        if kind == discord.CategoryChannel: self.guild.categories.append(channel)
        return channel

    def link_game(self):
        db.set_game_structure(self.game['id'], role_id=self.game_role.id, category_id=self.category.id, chat_id=self.chat.id, memes_id=None, lfg_id=None, create_voice_id=self.generator.id, has_lfg=False)
        self.game = db.get_game_by_id(self.game['id'])

    def snapshot(self): return cleanup.inspect_area(self.guild, db.get_game_by_id(self.game['id']))

    def event(self):
        return db.create_lfg_event(guild_id=1, game_id=self.game['id'], host_id=10, title='Gaming', start_at=int(time.time())+3600, max_players=5, invite_lead_minutes=15)

    def test_role_resolves_and_persists_id_after_rename(self):
        role, error = music.resolve_music_role(self.guild)
        self.assertIs(role, self.music); self.assertIsNone(error)
        self.music.name = 'Renamed music role'
        self.assertIs(music.resolve_music_role(self.guild)[0], self.music)
        self.guild.roles.remove(self.music)
        self.assertIsNone(music.resolve_music_role(self.guild)[0])

    def test_missing_ambiguous_or_privileged_role_fails_closed(self):
        self.role(4, 'Music Bots')
        self.assertIsNone(music.resolve_music_role(self.guild)[0])
        self.guild.roles.pop()
        self.music.permissions = discord.Permissions(administrator=True)
        self.assertIsNone(music.resolve_music_role(self.guild)[0])
        self.guild.roles.remove(self.music)
        self.assertIn('not found', music.resolve_music_role(self.guild)[1])

    async def test_text_voice_sync_preserves_other_overwrites_and_is_idempotent(self):
        before = self.chat.overwrites[self.game_role]
        first = await music.sync_music_access(self.guild)
        self.assertEqual(first['updated'], 3)
        self.assertIs(self.chat.overwrites[self.game_role], before)
        for name in music.TEXT_RIGHTS: self.assertTrue(getattr(self.chat.overwrites[self.music], name))
        for name in music.VOICE_RIGHTS: self.assertTrue(getattr(self.generator.overwrites[self.music], name))
        for name in music.DENIED_RIGHTS: self.assertFalse(getattr(self.chat.overwrites[self.music], name))
        second = await music.sync_music_access(self.guild)
        self.assertEqual(second['updated'], 0); self.assertEqual(second['correct'], 3)

    async def test_private_staff_unknown_and_mixed_category_excluded(self):
        staff = self.channel(200, 'STAFF', discord.CategoryChannel)
        secret = self.channel(201, 'mod-log', discord.TextChannel, staff)
        private = self.channel(103, 'private-ticket', discord.TextChannel, self.category)
        self.assertFalse(music.should_allow_music_bots(staff))
        self.assertFalse(music.should_allow_music_bots(secret))
        self.assertFalse(music.should_allow_music_bots(private))
        self.assertFalse(music.should_allow_music_bots(self.category))
        await music.sync_music_access(self.guild)
        for obj in (staff, secret, private, self.category): obj.set_permissions.assert_not_awaited()

    async def test_public_community_allowed_but_private_child_not_exposed(self):
        category = self.channel(200, '💬 COMMUNITY', discord.CategoryChannel)
        public = self.channel(201, 'bot-commands', discord.TextChannel, category)
        private = self.channel(202, 'private-ticket', discord.TextChannel, category)
        private.overwrites[self.everyone] = discord.PermissionOverwrite(view_channel=False)
        self.assertTrue(music.should_allow_music_bots(public))
        self.assertFalse(music.should_allow_music_bots(category))
        self.assertFalse(music.should_allow_music_bots(private))

    def test_unsynchronised_game_child_without_game_grant_is_private(self):
        self.chat.overwrites = {self.everyone: discord.PermissionOverwrite(view_channel=False)}
        self.assertFalse(music.should_allow_music_bots(self.chat))
        self.assertFalse(music.should_allow_music_bots(self.category))

    async def test_events_remain_allowed_while_read_only_boards_and_staff_are_excluded(self):
        events = self.channel(400, '🏆 EVENTS', discord.CategoryChannel)
        tournament = self.channel(401, 'tournaments', discord.TextChannel, events)
        community = self.channel(402, 'COMMUNITY', discord.CategoryChannel)
        suggestions = self.channel(403, 'suggestions', discord.TextChannel, community)
        start = self.channel(404, 'START HERE', discord.CategoryChannel)
        lfg_board = self.channel(405, 'looking-for-group', discord.TextChannel, start)
        staff = self.channel(406, 'STAFF', discord.CategoryChannel)
        inbox = self.channel(407, 'staff-suggestions', discord.TextChannel, staff)
        await music.sync_music_access(self.guild)
        tournament.set_permissions.assert_awaited_once()
        for channel in (suggestions, lfg_board, inbox):
            channel.set_permissions.assert_not_awaited()

    async def test_new_game_area_creation_has_music_overwrite(self):
        # Empty plan creates a fresh category, with inherited and explicit child grants.
        self.guild.channels.clear(); self.guild.categories.clear()
        db.deactivate_game(self.game['id'])
        async def category(**kwargs):
            created = self.channel(300, kwargs['name'], discord.CategoryChannel)
            created.overwrites = kwargs['overwrites']; return created
        async def text(name, **kwargs): return self.channel(301, name, discord.TextChannel, kwargs['category'])
        async def generator(name, **kwargs): return self.channel(302, name, discord.VoiceChannel, kwargs['category'])
        self.guild.create_category = AsyncMock(side_effect=category)
        self.guild.create_text_channel = AsyncMock(side_effect=text)
        self.guild.create_voice_channel = AsyncMock(side_effect=generator)
        plan = {'conflicts': [], 'role': self.game_role, 'category': None, 'category_name': '🎮 TEST GAME', 'channels': {'chat': {'existing': None}, 'create_voice': {'existing': None}}}
        await game_service.create_game_structure_confirmed(self.guild, db.get_game_by_id(self.game['id']), plan)
        created = self.guild.get_channel(300)
        self.assertTrue(created.overwrites[self.music].connect)
        self.assertTrue(self.guild.get_channel(301).overwrites[self.music].send_messages)
        self.assertTrue(self.guild.get_channel(302).overwrites[self.music].speak)

    async def test_global_temp_voice_contains_music_and_private_lobby_stays_private(self):
        category = self.channel(200, voice.GLOBAL_VOICE_CATEGORY, discord.CategoryChannel)
        generator = self.channel(201, voice.GLOBAL_CREATE_VOICE, discord.VoiceChannel, category)
        member = self.actor; member.guild = self.guild; member.display_name = 'Host'; member.move_to = AsyncMock()
        temp = self.channel(202, 'Temporary', discord.VoiceChannel, category)
        self.guild.create_voice_channel = AsyncMock(return_value=temp)
        cog = voice.VoiceGenerator(SimpleNamespace())
        await cog._create_global_temp_voice(member, generator)
        self.assertTrue(self.guild.create_voice_channel.call_args.kwargs['overwrites'][self.music].use_voice_activation)
        event = self.event(); event['visibility'] = 'private'
        self.guild.get_member.return_value = None
        overwrites = lfg.event_voice_overwrites(self.guild, event)
        self.assertFalse(overwrites[self.everyone].view_channel)
        self.assertTrue(overwrites[self.music].connect)

    async def test_game_temp_voice_contains_music_without_opening_everyone(self):
        member = self.actor; member.guild = self.guild; member.bot = False
        member.display_name = 'Host'; member.move_to = AsyncMock()
        temp = self.channel(103, 'Temporary', discord.VoiceChannel, self.category)
        self.guild.create_voice_channel = AsyncMock(return_value=temp)
        cog = voice.VoiceGenerator(SimpleNamespace())
        await cog.on_voice_state_update(member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.generator))
        overwrites = self.guild.create_voice_channel.call_args.kwargs['overwrites']
        self.assertTrue(overwrites[self.music].connect)
        self.assertFalse(overwrites[self.everyone].view_channel)

    def test_cleanup_candidates_protect_visible_active_and_unknown_areas(self):
        self.assertTrue(self.snapshot()['safe'])
        unknown = self.channel(300, 'Unknown', discord.CategoryChannel)
        rows, unknowns = cleanup.scan_areas(self.guild)
        self.assertIn(unknown, unknowns); self.assertEqual(len(rows), 1)
        db.set_game_selectable(self.game['id'], True)
        self.assertFalse(self.snapshot()['safe'])
        db.set_game_selectable(self.game['id'], False)
        with db.connect() as conn: conn.execute('UPDATE games SET active=1 WHERE id=?', (self.game['id'],))
        self.assertFalse(self.snapshot()['safe'])

    def test_scheduled_lobby_temp_voice_and_unknown_child_block_cleanup(self):
        event = self.event()
        self.assertFalse(self.snapshot()['safe'])
        db.set_lfg_event_status(event['id'], 'active')
        self.assertFalse(self.snapshot()['safe'])
        db.delete_lfg_event(event['id'])
        db.add_temp_voice(888, 10, self.game['id'])
        self.assertFalse(self.snapshot()['safe'])
        db.remove_temp_voice(888)
        self.channel(303, 'user-created', discord.TextChannel, self.category)
        self.assertFalse(self.snapshot()['safe'])

    async def test_confirmed_cleanup_only_removes_mapped_resources_and_keeps_game(self):
        unknown = self.channel(300, 'Unrelated', discord.CategoryChannel)
        role_id = self.game['role_id']
        result = await cleanup.delete_confirmed_area(self.guild, self.actor, self.snapshot())
        self.assertIn('Removed area', result)
        unknown.delete.assert_not_awaited()
        fresh = db.get_game_by_id(self.game['id'])
        self.assertEqual(fresh['role_id'], role_id); self.assertIsNone(fresh['category_id'])
        self.assertFalse(fresh['area_enabled'])

    async def test_conditions_changed_since_preview_skips_all_deletion(self):
        preview = self.snapshot()
        self.event()
        result = await cleanup.delete_confirmed_area(self.guild, self.actor, preview)
        self.assertIn('Skipped', result)
        for obj in (self.chat, self.generator, self.category): obj.delete.assert_not_awaited()

    async def test_partial_failure_keeps_category_and_remaining_links(self):
        self.generator.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        result = await cleanup.delete_confirmed_area(self.guild, self.actor, self.snapshot())
        self.assertIn('stopped', result)
        current = db.get_game_by_id(self.game['id'])
        self.assertIsNone(current['chat_channel_id'])
        self.assertEqual(current['create_voice_channel_id'], self.generator.id)
        self.assertEqual(current['category_id'], self.category.id)
        self.category.delete.assert_not_awaited()

    async def test_administrator_can_confirm_without_being_owner(self):
        self.actor.id = 20; self.actor.guild_permissions = discord.Permissions(administrator=True)
        result = await cleanup.delete_confirmed_area(self.guild, self.actor, self.snapshot())
        self.assertIn('Removed area', result)

    async def test_unauthorized_and_repeated_confirmations_do_not_delete(self):
        stranger = MagicMock(spec=discord.Member); stranger.id = 42; stranger.guild_permissions = discord.Permissions()
        with self.assertRaises(ValueError): await cleanup.delete_confirmed_area(self.guild, stranger, self.snapshot())
        request = SimpleNamespace(user=stranger, response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()), edit_original_response=AsyncMock())
        view = CleanupConfirm(self.guild, self.actor.id, self.snapshot())
        await view.confirm.callback(request)
        self.category.delete.assert_not_awaited()
        request.user = self.actor
        await view.confirm.callback(request)
        await view.confirm.callback(request)
        self.category.delete.assert_awaited_once()

    async def test_recheck_after_first_delete_preserves_new_unknown_child(self):
        original_delete = self.chat.delete.side_effect
        async def create_child(**kwargs):
            await original_delete(**kwargs)
            self.channel(400, 'new-user-channel', discord.TextChannel, self.category)
        self.chat.delete.side_effect = create_child
        result = await cleanup.delete_confirmed_area(self.guild, self.actor, self.snapshot())
        self.assertIn('Stopped', result)
        self.category.delete.assert_not_awaited()
        self.guild.get_channel(400).delete.assert_not_awaited()

    def test_cleanup_reservation_blocks_new_events(self):
        safety.cleaning.add(self.game['id'])
        with self.assertRaises(ValueError): self.event()

    async def test_preview_serializes_without_deleting(self):
        rows, unknown = cleanup.scan_areas(self.guild)
        view = CleanupPreview(self.guild, self.actor.id, rows, unknown)
        self.assertIn('Candidate', view.content())
        view.to_components()
        self.category.delete.assert_not_awaited()

    async def test_setup_confirmation_reports_music_and_cleanup_without_deletion(self):
        from cogs.server import ConfirmServerRepairView
        from services import server_setup_service
        request = SimpleNamespace(user=self.actor, client=SimpleNamespace(),
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock())
        with patch.object(server_setup_service, 'repair_server', AsyncMock(return_value=([], []))), patch.object(server_setup_service, 'analyze_server', return_value={}), patch.object(server_setup_service, 'render_summary', return_value='Inventory'):
            view = ConfirmServerRepairView(guild=self.guild, owner_id=10)
            await view.confirm.callback(request)
        content = request.edit_original_response.call_args.kwargs['content']
        self.assertIn('MUSIC BOT ACCESS', content)
        self.assertIn('Candidates available: 1', content)
        self.assertIn('Updated: 3', content)
        self.category.delete.assert_not_awaited()
