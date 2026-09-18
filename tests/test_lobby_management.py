"""Offline domain, Discord callback, persistence and resource regressions."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from database import db
from services import lobby_service as rules, lobby_dashboard as dashboard
from services.lfg_service import parse_server_datetime, render_event
from cogs import lfg, lobby_management as ui


class LobbyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(db, 'DB_PATH', Path(self.temp.name) / 'lobbies.db')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        db.init_db()
        self.start = int(time.time()) + 86400 * 5
        self.game = db.upsert_custom_game('Test Game', '🎮', 'Test')
        self.event = db.create_lfg_event(guild_id=1, game_id=self.game['id'], host_id=10, title='Test Lobby', start_at=self.start, max_players=5, invite_lead_minutes=15)
        self.eid = self.event['id']
        rules.join(self.eid, 1, 20)
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = 1
        self.guild.get_channel.return_value = None
        self.guild.get_member.return_value = None
        dashboard._locks.clear()
        dashboard._channel_locks.clear()

    def request(self, user_id=10):
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        user.guild_permissions.administrator = False
        response = SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock(), is_done=lambda: False)
        return SimpleNamespace(user=user, guild=self.guild, response=response, edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))

    def test_host_edits_and_rejects_other_actors_and_guilds(self):
        updated = rules.edit(self.eid, 1, 10, title='Changed', note='Hello', max_players=3, invite_lead_minutes=30)
        self.assertEqual((updated['title'], updated['max_players']), ('Changed', 3))
        for user in (20, 99):
            with self.assertRaises(ValueError): rules.edit(self.eid, 1, user, title='No')
        with self.assertRaises(ValueError): rules.edit(self.eid, 2, 10, title='No')
        for fields in ({'max_players': 1}, {'max_players': 100}, {'title': ''}, {'start_at': 0}, {'invite_lead_minutes': -1}, {'note': 'x' * 501}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): rules.edit(self.eid, 1, 10, **fields)

    def test_datetime_validation(self):
        for date, clock in [('nonsense', '20:00'), ('2000-01-01', '20:00'), ('2030-01-01', '25:00'), ('2030-03-31', '02:30'), ('2030-10-27', '02:30')]:
            with self.subTest(date=date, clock=clock), self.assertRaises(ValueError): parse_server_datetime(date, clock)
        self.assertGreater(parse_server_datetime('2030-01-01', '20:00'), time.time())

    def test_invites_duplicates_capacity_and_private_security(self):
        with db.connect() as conn:
            conn.execute("UPDATE lfg_events SET visibility='private',share_token='secret' WHERE id=?", (self.eid,))
        with self.assertRaises(ValueError): rules.invite(self.eid, 1, 20, 30)
        self.assertTrue(rules.invite(self.eid, 1, 10, 30))
        self.assertFalse(rules.invite(self.eid, 1, 10, 30))
        self.assertEqual(rules.join(self.eid, 1, 30), 'joined')
        with self.assertRaises(ValueError): rules.join(self.eid, 1, 40)
        self.assertEqual(rules.join(self.eid, 1, 40, 'secret'), 'joined')
        rules.edit(self.eid, 1, 10, max_players=4)
        rules.invite(self.eid, 1, 10, 50)
        self.assertEqual(rules.join(self.eid, 1, 50), 'full')
        rules.remove(self.eid, 1, 10, 40)
        with self.assertRaises(ValueError): rules.join(self.eid, 1, 40, 'secret')
        self.assertEqual(rules.join(self.eid, 1, 50), 'joined')

    def test_proposals_permissions_dedup_decisions_and_reminder_reset(self):
        pid = rules.propose(self.eid, 1, 20, self.start + 3600, 'Later please')
        with self.assertRaises(ValueError): rules.propose(self.eid, 1, 99, self.start + 7200)
        with self.assertRaises(ValueError): rules.propose(self.eid, 1, 10, self.start + 3600)
        with self.assertRaises(ValueError): rules.decide(self.eid, 1, 20, pid, 'ACCEPTED')
        db.claim_lfg_voice_notification(self.eid, 20, int(time.time()))
        updated = rules.decide(self.eid, 1, 10, pid, 'ACCEPTED')
        self.assertEqual(updated['start_at'], self.start + 3600)
        self.assertFalse(db.has_lfg_voice_notification(self.eid, 20))
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM lfg_time_proposals WHERE id=?', (pid,)).fetchone()[0], 'ACCEPTED')
        with self.assertRaises(ValueError): rules.decide(self.eid, 1, 10, pid, 'DECLINED')
        second = rules.propose(self.eid, 1, 20, self.start + 7200)
        rules.decide(self.eid, 1, 10, second, 'DECLINED')
        third = rules.propose(self.eid, 1, 20, self.start + 10800)
        rules.decide(self.eid, 1, 20, third, 'WITHDRAWN')
        self.assertEqual(rules.proposals(self.eid), [])

    def test_terminal_states_and_admin_override(self):
        with self.assertRaises(ValueError): rules.end(self.eid, 1, 99, 'cancelled')
        with self.assertRaises(ValueError): rules.end(self.eid, 1, 99, 'completed', administrator=True)
        rules.end(self.eid, 1, 99, 'cancelled', administrator=True)
        for operation in (lambda: rules.edit(self.eid, 1, 10, title='No'), lambda: rules.propose(self.eid, 1, 20, self.start+3600), lambda: rules.join(self.eid, 1, 30)):
            with self.assertRaises(ValueError): operation()
        self.assertIn('CANCELLED', render_event(self.guild, db.get_lfg_event(self.eid)))
        with db.connect() as conn: conn.execute("UPDATE lfg_events SET status='completed' WHERE id=?", (self.eid,))
        with self.assertRaises(ValueError): rules.propose(self.eid, 1, 20, self.start+3600)
        self.assertIn('COMPLETED', render_event(self.guild, db.get_lfg_event(self.eid)))

    def test_reschedule_rejects_open_voice_and_stale_voice_claim(self):
        rules.edit(self.eid, 1, 10, start_at=self.start + 3600)
        self.assertFalse(db.claim_lfg_event_voice(self.eid, 123, expected_start=self.start))
        self.assertTrue(db.claim_lfg_event_voice(self.eid, 123, expected_start=self.start + 3600))
        with self.assertRaises(ValueError): rules.edit(self.eid, 1, 10, start_at=self.start+7200)
        with self.assertRaises(ValueError): rules.propose(self.eid, 1, 20, self.start+7200)

    def test_schema_idempotence_and_persistence(self):
        pid = rules.propose(self.eid, 1, 20, self.start+3600)
        db.init_db(); db.init_db()
        self.assertEqual(rules.proposals(self.eid)[0]['id'], pid)
        self.assertEqual(db.get_lfg_event(self.eid)['title'], 'Test Lobby')

    async def test_panels_limits_and_member_host_separation(self):
        for user_id in (10, 20):
            panel = ui.LobbyPanel(self.event, user_id)
            panel.to_components()
            labels = [o.label for c in panel.children if isinstance(c, discord.ui.Select) for o in c.options]
            self.assertEqual('Cancel Lobby' in labels, user_id == 10)
            self.assertEqual('Leave Lobby' in labels, user_id == 20)
            self.assertIn('Suggest New Time', labels)
            panel.stop()
        self.assertTrue(lfg.LFGEventView(self.eid).is_persistent())
        manager = lfg.LFGManageView(10, [self.event] * 26)
        self.assertFalse(manager.next_page.disabled)
        manager.stop()

    async def test_modal_rechecks_permissions(self):
        request = self.request(20)
        modal = ui.EditModal(self.event)
        await modal.on_submit(request)
        request.response.send_message.assert_awaited_once()
        self.assertEqual(db.get_lfg_event(self.eid)['title'], 'Test Lobby')

    async def test_dashboard_reuses_message_after_join_leave_edit_time_and_end(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 100
        message = SimpleNamespace(id=200, author=SimpleNamespace(id=self.guild.me.id), edit=AsyncMock())
        channel.fetch_message = AsyncMock(return_value=message)
        channel.send = AsyncMock(return_value=message)
        self.guild.get_channel.return_value = channel
        db.add_lfg_event_message(self.eid, channel_id=100, message_id=200)
        with patch.object(dashboard, 'dashboard_channel', AsyncMock(return_value=channel)):
            await lfg.refresh_event_posts(self.guild, self.eid)
            rules.join(self.eid, 1, 30)
            await lfg.refresh_event_posts(self.guild, self.eid)
            self.assertIn('3/5', message.edit.call_args.kwargs['content'])
            rules.remove(self.eid, 1, 30, 30)
            await lfg.refresh_event_posts(self.guild, self.eid)
            self.assertIn('2/5', message.edit.call_args.kwargs['content'])
            rules.edit(self.eid, 1, 10, title='New title')
            await lfg.refresh_event_posts(self.guild, self.eid)
            self.assertIn('New title', message.edit.call_args.kwargs['content'])
            pid = rules.propose(self.eid, 1, 20, self.start+3600)
            event = rules.decide(self.eid, 1, 10, pid, 'ACCEPTED')
            with patch.object(ui, 'notify_members', AsyncMock()) as notify:
                await ui.changed(self.guild, event, time_changed=True)
                notify.assert_awaited_once()
            self.assertIn(str(self.start+3600), message.edit.call_args.kwargs['content'])
            rules.end(self.eid, 1, 10, 'completed')
            await lfg.refresh_event_posts(self.guild, self.eid)
            self.assertIsNone(message.edit.call_args.kwargs['view'])
            self.assertIn('COMPLETED', message.edit.call_args.kwargs['content'])
        channel.send.assert_not_awaited()
        self.assertEqual(db.get_lfg_event(self.eid)['dashboard_message_id'], 200)

    async def test_private_dashboard_never_resolves_public_channel(self):
        with db.connect() as conn: conn.execute("UPDATE lfg_events SET visibility='private' WHERE id=?", (self.eid,))
        with patch.object(dashboard, 'dashboard_channel', AsyncMock()) as public:
            await dashboard.sync_card(self.guild, db.get_lfg_event(self.eid), None)
            public.assert_not_awaited()

    async def test_voice_deletion_only_tracked_empty_channel_and_failure_keeps_id(self):
        unrelated = MagicMock(spec=discord.VoiceChannel)
        unrelated.id = 987
        unrelated.delete = AsyncMock()
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id = 123; voice.members = [object()]; voice.delete = AsyncMock()
        self.guild.get_channel.side_effect = lambda cid: voice if cid == 123 else unrelated
        db.set_lfg_event_voice(self.eid, 123)
        event = db.get_lfg_event(self.eid)
        self.assertFalse(await lfg.delete_event_voice(self.guild, event))
        voice.delete.assert_not_awaited()
        voice.members = []
        voice.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        self.assertFalse(await lfg.delete_event_voice(self.guild, event))
        self.assertEqual(db.get_lfg_event(self.eid)['voice_channel_id'], 123)
        voice.delete.side_effect = None
        self.assertTrue(await lfg.delete_event_voice(self.guild, event))
        unrelated.delete.assert_not_awaited()

    async def test_atomic_capacity_under_parallel_threads(self):
        rules.edit(self.eid, 1, 10, max_players=3)
        results = await asyncio.gather(*(asyncio.to_thread(rules.join, self.eid, 1, uid) for uid in range(30, 35)))
        self.assertEqual(results.count('joined'), 1)
        self.assertEqual(results.count('full'), 4)

    async def test_legacy_join_rechecks_private_invitation(self):
        with db.connect() as conn: conn.execute("UPDATE lfg_events SET visibility='private' WHERE id=?", (self.eid,))
        member = MagicMock(spec=discord.Member); member.id = 99
        self.assertEqual(await lfg._finish_join(self.guild, self.event, member), 'unavailable')

    async def test_new_dashboard_card_and_deleted_card_repair_are_idempotent(self):
        channel = MagicMock(spec=discord.TextChannel); channel.id = 100
        message = SimpleNamespace(id=200, author=SimpleNamespace(id=self.guild.me.id), edit=AsyncMock())
        channel.send = AsyncMock(return_value=message)
        async def history(**kwargs):
            if False: yield None
        channel.history = history
        channel.fetch_message = AsyncMock(return_value=message)
        with patch.object(dashboard, 'dashboard_channel', AsyncMock(return_value=channel)):
            await dashboard.sync_card(self.guild, db.get_lfg_event(self.eid), lfg.LFGEventView(self.eid))
            await dashboard.sync_card(self.guild, db.get_lfg_event(self.eid), lfg.LFGEventView(self.eid))
            channel.send.assert_awaited_once()
            channel.fetch_message.side_effect = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'missing')
            await dashboard.sync_card(self.guild, db.get_lfg_event(self.eid), lfg.LFGEventView(self.eid))
            self.assertEqual(channel.send.await_count, 2)
            channel.fetch_message.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
            with self.assertRaises(discord.Forbidden):
                await dashboard.sync_card(self.guild, db.get_lfg_event(self.eid), lfg.LFGEventView(self.eid))
            self.assertEqual(channel.send.await_count, 2)

    async def test_voice_creation_claim_and_existing_room_reuse(self):
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id = 555; voice.delete = AsyncMock()
        self.guild.create_voice_channel = AsyncMock(return_value=voice)
        self.guild.me = None
        with patch.object(lfg, 'refresh_event_posts', AsyncMock()):
            result = await lfg.create_event_voice(self.guild, self.event)
            self.assertIs(result, voice)
            self.assertEqual(db.get_lfg_event(self.eid)['voice_channel_id'], 555)
            self.guild.get_channel.return_value = voice
            self.assertIs(await lfg.create_event_voice(self.guild, db.get_lfg_event(self.eid)), voice)
        self.guild.create_voice_channel.assert_awaited_once()
        voice.delete.assert_not_awaited()

    async def test_end_cleanup_delays_and_retries_without_losing_history(self):
        rules.end(self.eid, 1, 10, 'cancelled')
        with patch.object(lfg, 'delete_event_posts', AsyncMock(return_value=True)) as posts, patch.object(lfg, 'delete_event_voice', AsyncMock(return_value=True)), patch.object(lfg, 'delete_private_event_channel', AsyncMock(return_value=True)):
            await dashboard.cleanup_ended(self.guild)
            posts.assert_not_awaited()
            with db.connect() as conn: conn.execute('UPDATE lfg_events SET ended_at=? WHERE id=?', (int(time.time())-86401, self.eid))
            posts.return_value = False
            await dashboard.cleanup_ended(self.guild)
            self.assertIsNotNone(db.get_lfg_event(self.eid)['ended_at'])
            posts.return_value = True
            await dashboard.cleanup_ended(self.guild)
            self.assertIsNone(db.get_lfg_event(self.eid)['ended_at'])
            self.assertEqual(db.get_lfg_event(self.eid)['status'], 'cancelled')

    async def test_host_proposal_dm_buttons_recheck_owner_and_survive_registration(self):
        pid = rules.propose(self.eid, 1, 20, self.start+3600)
        view = ui.ProposalDMView(self.eid, pid)
        self.assertTrue(view.is_persistent())
        request = self.request(20)
        request.client = SimpleNamespace(get_guild=lambda _: self.guild)
        self.guild.get_member.return_value = request.user
        await view.children[0].callback(request)
        self.assertEqual(db.get_lfg_event(self.eid)['start_at'], self.start)
        request = self.request(10)
        request.client = SimpleNamespace(get_guild=lambda _: self.guild)
        with patch.object(ui, 'changed', AsyncMock()):
            await view.children[0].callback(request)
        self.assertEqual(db.get_lfg_event(self.eid)['start_at'], self.start+3600)

    def test_existing_schema_migration_keeps_rows(self):
        with db.connect() as conn:
            for column in ('note', 'dashboard_channel_id', 'dashboard_message_id', 'ended_at'):
                conn.execute(f'ALTER TABLE lfg_events DROP COLUMN {column}')
            conn.execute('DROP TABLE lfg_time_proposals')
        db.init_db()
        self.assertEqual(db.get_lfg_event(self.eid)['host_id'], 10)
        self.assertEqual(len(db.get_lfg_event_members(self.eid)), 2)
        self.assertEqual(db.get_lfg_event(self.eid)['note'], '')


if __name__ == '__main__': unittest.main()
