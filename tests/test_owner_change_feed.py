"""Persistent per-change notifications, private confirmation and safe delivery."""
import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import test_owner_changelog as fixtures
from cogs import owner_changelog as log
from database import db
from services import owner_change_feed as feed
from services import structure_adoption_service as structure


class OwnerFeedTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OwnerLogTests.setUp
    role = fixtures.OwnerLogTests.role
    member = fixtures.OwnerLogTests.member
    request = fixtures.OwnerLogTests.request
    channel = fixtures.OwnerLogTests.channel

    async def asyncSetUp(self):
        feed._locks.clear()
        self.room = self.channel()
        self.guild.channels.append(self.room)
        self.guild.text_channels.append(self.room)
        db.set_setting(log.key(self.guild), self.room.id)
        self.messages = {}
        self.edits = []
        self.next_id = 10000

        async def send(*, content, view, allowed_mentions):
            self.next_id += 1
            message = SimpleNamespace(id=self.next_id, content=content, author=self.bot,
                                      channel=self.room, guild=self.guild, view=view)
            async def edit(**kwargs):
                self.edits.append((message.id, kwargs))
                message.content, message.view = kwargs['content'], kwargs['view']
            message.edit = AsyncMock(side_effect=edit)
            self.messages[message.id] = message
            return message
        self.room.send = AsyncMock(side_effect=send)
        async def fetch(mid):
            if mid not in self.messages:
                raise discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'missing')
            return self.messages[mid]
        self.room.fetch_message = AsyncMock(side_effect=fetch)
        feed.enable(self.guild)

    def change(self, *, action='channel_update', reversible=True, guild=None):
        return structure.record_change(guild or self.guild, 'channel', 'secret-private-resource', 8888,
            self.owner.id, action,
            {'name': 'secret-before', 'category_id': 77, 'position': 1, 'token': 'never-publish-this'},
            {'name': 'secret-after', 'category_id': 77, 'position': 1}, reversible=reversible)

    def message(self, change):
        state = feed.load_notice(self.guild.id, change['id'])
        return self.messages[state['message_id']]

    def notice_request(self, change, member=None):
        request = self.request(member)
        request.channel_id = self.room.id
        request.message = self.message(change)
        return request

    async def test_structure_announcement_immediately_posts_one_notice_and_repeated_calls_do_not_duplicate(self):
        change = self.change()
        with patch('services.server_log_service.emit', AsyncMock()):
            await structure._announce(self.guild, change, 'ignored title', 'secret description')
            await structure._announce(self.guild, change, 'ignored title', 'secret description')
        await asyncio.gather(feed.flush(self.guild), feed.flush(self.guild))
        self.room.send.assert_awaited_once()
        message = self.message(change)
        self.assertIn('Channel changed', message.content)
        self.assertTrue(message.view.is_persistent())
        self.assertEqual(message.view.undo.label, 'Undo')
        self.assertFalse(message.view.undo.disabled)
        # The short resource/action summary is intentionally visible directly
        # in owner-changelog; raw logical keys, IDs, tokens and private description
        # stay behind owner-only Details.
        self.assertIn('#secret-after', message.content)
        self.assertIn('renamed from', message.content)
        for forbidden in ('secret-private-resource', 'never-publish-this', '<@71>', '8888', 'secret description'):
            self.assertNotIn(forbidden, message.content)
        mentions = self.room.send.call_args.kwargs['allowed_mentions']
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.guild.fetch_channels.assert_not_awaited()
        self.room.history.assert_not_called()

    async def test_delete_notice_shows_resource_and_action_without_opening_details(self):
        change = structure.record_change(
            self.guild, 'channel', 'looking-for-group', 8899, self.owner.id, 'channel_delete',
            {'name': '🎯・looking-for-group', 'category_id': 77, 'position': 4},
            {'name': '🎯・looking-for-group', 'deleted': True},
            reversible=False,
        )
        await feed.flush(self.guild)
        notice = self.message(change).content
        self.assertIn('#🎯・looking-for-group', notice)
        self.assertIn('was deleted', notice)
        self.assertIn('Deleted history is not recoverable automatically', notice)
        self.assertNotIn('Category ID', notice)
        self.assertNotIn('Position', notice)

    async def test_two_changes_produce_two_separate_messages_in_order(self):
        first, second = self.change(), self.change(action='category_update')
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, 2)
        self.assertLess(self.message(first).id, self.message(second).id)

    async def test_restart_retains_message_binding_without_resending(self):
        change = self.change()
        await feed.flush(self.guild)
        feed._locks.clear()
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        request = self.notice_request(change)
        with patch.object(structure, 'undo_change', AsyncMock()) as undo:
            await log.ChangeNotice().undo.callback(request)
        undo.assert_not_awaited()
        self.assertTrue(request.response.defer.call_args.kwargs['ephemeral'])
        self.assertIn('secret-before', request.edit_original_response.call_args.kwargs['content'])
        confirm = request.edit_original_response.call_args.kwargs['view']
        self.assertIsInstance(confirm, log.Undo)
        self.assertEqual(confirm.children[0].label, 'Confirm Undo')
        self.assertTrue(any(c.label == 'Cancel' for c in confirm.children))

    async def test_nonowner_administrator_is_denied_before_history_lookup(self):
        change = self.change()
        await feed.flush(self.guild)
        request = self.notice_request(change, self.admin)
        with patch.object(feed, 'change_for_message') as lookup:
            await log.ChangeNotice().undo.callback(request)
        lookup.assert_not_called()
        request.response.defer.assert_not_awaited()
        self.assertTrue(request.response.send_message.call_args.kwargs['ephemeral'])

    async def test_forged_message_id_or_wrong_channel_cannot_open_details(self):
        change = self.change()
        await feed.flush(self.guild)
        request = self.notice_request(change)
        request.message = SimpleNamespace(id=99999, author=self.bot)
        await log.ChangeNotice().review.callback(request)
        self.assertNotIn('secret-', request.edit_original_response.call_args.kwargs['content'])
        request = self.notice_request(change)
        request.channel_id = 999
        await log.ChangeNotice().undo.callback(request)
        self.assertIsNone(request.edit_original_response.call_args.kwargs['view'])

    async def test_author_and_current_owner_are_rechecked_after_acknowledgment(self):
        change = self.change()
        await feed.flush(self.guild)
        request = self.notice_request(change)
        request.message = SimpleNamespace(id=request.message.id, author=self.admin)
        await log.ChangeNotice().undo.callback(request)
        self.assertIsNone(request.edit_original_response.call_args.kwargs['view'])
        request = self.notice_request(change)
        async def transferred(**kwargs):
            self.guild.owner_id = self.admin.id
        request.response.defer.side_effect = transferred
        await log.ChangeNotice().undo.callback(request)
        self.assertIsNone(request.edit_original_response.call_args.kwargs['view'])

    async def test_confirm_undo_is_single_use_updates_original_notice_and_does_not_repost(self):
        change = self.change()
        await feed.flush(self.guild)
        request = self.notice_request(change)
        await log.ChangeNotice().undo.callback(request)
        view = request.edit_original_response.call_args.kwargs['view']
        async def undo(guild, owner, change_id):
            with db.connect() as conn:
                conn.execute("UPDATE structure_change_log SET status='UNDONE',undone_at=?,undone_by=? WHERE id=?",
                             (int(time.time()), owner.id, change_id))
        with patch.object(structure, 'undo_change', AsyncMock(side_effect=undo)) as action:
            await view.children[0].callback(self.request())
            await view.children[0].callback(self.request())
        action.assert_awaited_once()
        self.room.send.assert_awaited_once()
        self.assertIn('Undone', self.message(change).content)
        self.assertTrue(self.message(change).view.undo.disabled)
        await feed.flush(self.guild)
        self.assertEqual(len(self.edits), 1)

    async def test_expired_or_cancelled_confirmation_does_not_undo(self):
        change = self.change()
        for mode in ('expired', 'cancelled'):
            view = log.Undo(self.guild.id, self.owner.id, change)
            if mode == 'expired':
                view.expires = 0
            else:
                cancel = next(c for c in view.children if c.label == 'Cancel')
                await cancel.callback(self.request())
            with patch.object(structure, 'undo_change', AsyncMock()) as action:
                await view.children[0].callback(self.request())
            action.assert_not_awaited()

    async def test_stale_confirmation_keeps_newer_state(self):
        change = self.change()
        view = log.Undo(self.guild.id, self.owner.id, change)
        with db.connect() as conn:
            conn.execute("UPDATE structure_change_log SET status='UNDONE' WHERE id=?", (change['id'],))
        with patch.object(structure, 'undo_change', AsyncMock()) as action:
            await view.children[0].callback(self.request())
        action.assert_not_awaited()

    async def test_irreversible_delete_has_disabled_undo_and_private_replacement_review(self):
        change = self.change(action='channel_delete', reversible=False)
        await feed.flush(self.guild)
        self.assertTrue(self.message(change).view.undo.disabled)
        self.assertIn('history is not recoverable', self.message(change).content)
        request = self.notice_request(change)
        await log.ChangeNotice().review.callback(request)
        view = request.edit_original_response.call_args.kwargs['view']
        self.assertTrue(any(c.label == 'Confirm Replacement' for c in view.children))

    async def test_uncertain_send_is_not_automatically_retried_after_restart(self):
        change = self.change()
        self.room.send.side_effect = discord.HTTPException(SimpleNamespace(status=502, reason='Bad Gateway'), 'uncertain')
        await feed.flush(self.guild)
        feed._locks.clear()
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        self.assertEqual(feed.load_notice(self.guild.id, change['id'])['state'], 'UNCERTAIN')
        self.assertIsNotNone(structure.get_change(self.guild, change['id']))

    async def test_definite_permission_rejection_is_retryable_without_duplicate(self):
        change = self.change()
        sender = self.room.send.side_effect
        self.room.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        await feed.flush(self.guild)
        self.assertEqual(feed.load_notice(self.guild.id, change['id'])['state'], 'PENDING')
        self.room.send.side_effect = sender
        await feed.flush(self.guild)
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, 2)
        self.assertEqual(len(self.messages), 1)

    async def test_stale_inflight_reservation_is_not_resent(self):
        change = self.change()
        feed._store(self.guild.id, change['id'], {'state': 'SENDING', 'channel_id': self.room.id})
        await feed.flush(self.guild)
        self.room.send.assert_not_awaited()
        self.assertEqual(feed.load_notice(self.guild.id, change['id'])['state'], 'UNCERTAIN')

    async def test_new_activation_does_not_flood_history_but_new_change_is_published(self):
        old = self.change()
        with db.connect() as conn:
            conn.execute('DELETE FROM settings WHERE key=?', (feed.cursor_key(self.guild.id),))
        feed.enable(self.guild)
        await feed.flush(self.guild)
        self.room.send.assert_not_awaited()
        new = self.change()
        await feed.flush(self.guild)
        self.assertIsNone(feed.load_notice(self.guild.id, old['id']))
        self.assertIn(str(new['id']), self.message(new).content)

    async def test_deleted_notice_stays_deleted_when_undo_status_changes(self):
        change = self.change()
        await feed.flush(self.guild)
        self.messages.clear()
        with db.connect() as conn:
            conn.execute("UPDATE structure_change_log SET status='UNDONE' WHERE id=?", (change['id'],))
        await feed.flush(self.guild)
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        self.assertEqual(feed.load_notice(self.guild.id, change['id'])['state'], 'DELETED')

    async def test_unconfigured_or_exposed_destination_sends_nothing_and_preserves_pending_history(self):
        self.change()
        db.set_setting(log.key(self.guild), '')
        await feed.flush(self.guild)
        self.room.send.assert_not_awaited()
        db.set_setting(log.key(self.guild), self.room.id)
        self.room.overwrites[self.normal] = discord.PermissionOverwrite(view_channel=True)
        await feed.flush(self.guild)
        self.room.send.assert_not_awaited()
        self.room.overwrites.pop(self.normal)
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()

    async def test_flush_is_bounded_and_cross_guild_history_is_excluded(self):
        self.change(guild=SimpleNamespace(id=2))
        for _ in range(feed.BATCH_SIZE + 2):
            self.change()
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, feed.BATCH_SIZE)
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, feed.BATCH_SIZE + 2)

    async def test_terminal_refresh_failure_retries_edit_not_send(self):
        change = self.change()
        await feed.flush(self.guild)
        message = self.message(change)
        editor = message.edit.side_effect
        message.edit.side_effect = discord.HTTPException(SimpleNamespace(status=503, reason='Unavailable'), 'busy')
        with db.connect() as conn:
            conn.execute("UPDATE structure_change_log SET status='RESTORED' WHERE id=?", (change['id'],))
        await feed.flush(self.guild)
        message.edit.side_effect = editor
        await feed.flush(self.guild)
        self.assertEqual(message.edit.await_count, 2)
        self.room.send.assert_awaited_once()

    async def test_new_cog_registers_restart_safe_notice_handler_without_per_record_views(self):
        bot = SimpleNamespace(add_view=lambda view: self.registered.append(view))
        self.registered = []
        cog = log.OwnerChangeLog(bot)
        with patch.object(cog.feed_updates, 'start'):
            await cog.cog_load()
        self.assertEqual(len(self.registered), 2)
        self.assertTrue(all(view.is_persistent() for view in self.registered))
        self.assertIsInstance(self.registered[1], log.ChangeNotice)
