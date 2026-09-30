"""Offline regressions for readable owner history and grouped category notices."""
import asyncio
import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import test_owner_change_feed as fixtures
from cogs import owner_changelog as log
from database import db
from services import owner_change_display as display
from services import owner_change_feed as feed
from services import structure_adoption_service as structure


class ReadableOwnerTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OwnerFeedTests.setUp
    asyncSetUp = fixtures.OwnerFeedTests.asyncSetUp
    role = fixtures.OwnerFeedTests.role
    member = fixtures.OwnerFeedTests.member
    request = fixtures.OwnerFeedTests.request
    channel = fixtures.OwnerFeedTests.channel
    message = fixtures.OwnerFeedTests.message
    notice_request = fixtures.OwnerFeedTests.notice_request
    change = fixtures.OwnerFeedTests.change

    async def asyncTearDown(self):
        feed.cancel_pending_flushes()
        await asyncio.sleep(0)

    def record(self, kind, rid, action, before, after, *, age=10, reversible=False, guild=None):
        guild = guild or self.guild
        row = structure.record_change(guild, kind, f'discord:{kind}:{rid}', rid, None,
                                      action, before, after, reversible=reversible)
        with db.connect() as conn:
            conn.execute('UPDATE structure_change_log SET created_at=? WHERE id=?',
                         (time.time() - age, row['id']))
        return structure.get_change(guild, row['id'])

    def move(self, rid=8801, parent=8800, **extra):
        before = {'id': rid, 'name': f'private-chat-{rid}', 'position': 1, 'category_id': parent}
        after = dict(before, category_id=None, _fields=['category_id'])
        after.update(extra)
        return self.record('channel', rid, 'observed_channel_update', before, after)

    def deletion(self, rid=8800, **kwargs):
        return self.record('category', rid, 'observed_category_delete',
                           {'id': rid, 'name': f'Secret game {rid}', 'position': 1},
                           {'id': rid, 'deleted': True}, **kwargs)

    async def test_three_moves_then_category_delete_send_one_notice_keep_four_rows(self):
        children = [self.move(8801 + n) for n in range(3)]
        root = self.deletion()
        before = copy.deepcopy(structure.recent_changes(self.guild))
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        notice = self.message(root)
        self.assertIn('Category removed', notice.content)
        self.assertIn('3 related channel moves', notice.content)
        self.assertEqual([button.label for button in notice.view.children], ['View details'])
        self.assertEqual(before, structure.recent_changes(self.guild))
        self.assertEqual([row['id'] for row in feed.related_changes(self.guild, root['id'])],
                         [row['id'] for row in children])
        for row in children:
            self.assertEqual(feed.load_notice(self.guild.id, row['id'])['state'], 'GROUPED')

    async def test_parent_event_before_children_is_also_grouped(self):
        root = self.deletion()
        self.move()
        self.move(8802)
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        self.assertIn('2 related channel moves', self.message(root).content)

    async def test_same_time_other_category_is_not_absorbed(self):
        self.move()
        other = self.move(9901, 9900)
        root = self.deletion()
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, 2)
        self.assertEqual(len(feed.related_changes(self.guild, root['id'])), 1)
        self.assertEqual(feed.load_notice(self.guild.id, other['id'])['state'], 'SENT')

    async def test_rename_and_permission_changes_are_never_hidden_in_group(self):
        self.move()
        rename = self.move(8802, name='Other name')
        permissions = self.move(8803, overwrites={'1': [1024, 0]})
        topic = self.move(8804, topic='Separate important change')
        root = self.deletion()
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, 4)
        self.assertEqual(len(feed.related_changes(self.guild, root['id'])), 1)
        for row in (rename, permissions, topic):
            self.assertEqual(feed.load_notice(self.guild.id, row['id'])['state'], 'SENT')

    async def test_too_old_detach_stays_individual(self):
        move = self.move()
        with db.connect() as conn:
            conn.execute('UPDATE structure_change_log SET created_at=created_at-30 WHERE id=?', (move['id'],))
        root = self.deletion()
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, 2)
        self.assertEqual(feed.related_changes(self.guild, root['id']), [])

    async def test_no_parent_event_does_not_hide_move(self):
        row = self.move()
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        self.assertIn('Channel changed', self.message(row).content)

    async def test_offline_differences_are_not_claimed_as_category_deletion_group(self):
        self.move()
        root = self.record('category', 8800, 'observed_category_offline',
                           {'id': 8800, 'name': 'Secret category'}, {'id': 8800, 'deleted': True})
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, 2)
        self.assertFalse(feed.related_changes(self.guild, root['id']))

    async def test_other_guild_history_cannot_join_group(self):
        foreign = SimpleNamespace(id=2)
        self.deletion(guild=foreign)
        row = self.move()
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        self.assertEqual(feed.load_notice(self.guild.id, row['id'])['state'], 'SENT')

    async def test_sent_children_are_not_retroactively_removed_or_resent(self):
        move = self.move()
        await feed.flush(self.guild)
        root = self.deletion()
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, 2)
        self.assertEqual(feed.load_notice(self.guild.id, move['id'])['state'], 'SENT')
        self.assertEqual(feed.related_changes(self.guild, root['id']), [])
        self.message(move).edit.assert_not_awaited()

    async def test_restart_retains_one_message_and_the_member_binding(self):
        self.move()
        root = self.deletion()
        await feed.flush(self.guild)
        feed._locks.clear()
        feed.cancel_pending_flushes()
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        request = self.notice_request(root)
        await log.ChangeNotice().review.callback(request)
        self.assertIn('private-chat-8801', request.edit_original_response.call_args.kwargs['content'])
        self.assertIsInstance(request.edit_original_response.call_args.kwargs['view'], log.GroupDetails)

    async def test_group_definite_failure_retries_root_not_children(self):
        self.move()
        root = self.deletion()
        sender = self.room.send.side_effect
        self.room.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        await feed.flush(self.guild)
        self.assertEqual(feed.load_notice(self.guild.id, root['id'])['state'], 'PENDING')
        self.room.send.side_effect = sender
        feed._locks.clear()
        await feed.flush(self.guild)
        await feed.flush(self.guild)
        self.assertEqual(self.room.send.await_count, 2)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(len(feed.related_changes(self.guild, root['id'])), 1)

    async def test_group_uncertain_send_is_not_retried_after_restart(self):
        self.move()
        root = self.deletion()
        self.room.send.side_effect = discord.HTTPException(SimpleNamespace(status=502, reason='Bad Gateway'), 'uncertain')
        await feed.flush(self.guild)
        feed._locks.clear()
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        self.assertEqual(feed.load_notice(self.guild.id, root['id'])['state'], 'UNCERTAIN')
        self.assertEqual(len(structure.recent_changes(self.guild)), 2)

    async def test_concurrent_flushes_send_one_group(self):
        self.move()
        self.deletion()
        await asyncio.gather(feed.flush(self.guild), feed.flush(self.guild))
        self.room.send.assert_awaited_once()

    async def test_short_wait_delivers_without_another_manual_command(self):
        with patch.object(feed, 'GROUP_WINDOW', 0.05):
            root = self.deletion(age=0)
            await feed.flush(self.guild)
            self.room.send.assert_not_awaited()
            pending = feed._deferred[self.guild.id]
            await asyncio.wait_for(asyncio.shield(pending), timeout=2)
        self.room.send.assert_awaited_once()
        self.assertEqual(feed.load_notice(self.guild.id, root['id'])['state'], 'SENT')

    async def test_normal_rename_is_not_delayed(self):
        row = self.change()
        await feed.flush(self.guild)
        self.room.send.assert_awaited_once()
        self.assertNotIn(self.guild.id, feed._deferred)
        self.assertIn('Undo', [item.label for item in self.message(row).view.children])

    async def test_group_names_and_ids_never_appear_in_shared_notice(self):
        self.move()
        root = self.deletion()
        await feed.flush(self.guild)
        text = self.message(root).content
        for forbidden in ('Secret game', 'private-chat', '8800', '8801', 'discord:', 'category_id', 'APPLIED'):
            self.assertNotIn(forbidden, text)
        request = self.notice_request(root, self.admin)
        with patch.object(feed, 'related_changes') as read:
            await log.ChangeNotice().review.callback(request)
        read.assert_not_called()
        self.assertTrue(request.response.send_message.call_args.kwargs['ephemeral'])

    async def test_private_details_use_saved_category_name_even_after_deletion(self):
        move = self.move()
        self.deletion()
        text = log.details(move, self.guild)
        self.assertIn('private-chat-8801', text)
        self.assertIn('Secret game 8800', text)
        self.assertIn('No category', text)
        for forbidden in ('category_id', 'discord:channel:', 'APPLIED', 'None', 'Actor ID'):
            self.assertNotIn(forbidden, text)

    async def test_unknown_historical_name_is_not_guessed_from_another_resource(self):
        move = self.move(parent=777777)
        self.deletion()
        text = log.details(move, self.guild)
        self.assertIn('Name unavailable', text)
        self.assertNotIn('777777', text)
        self.assertNotIn('Secret game', text)

    async def test_legacy_rows_get_readable_names_without_migration(self):
        row = self.change()
        text = log.details(row, self.guild)
        self.assertIn('secret-before', text)
        self.assertIn('secret-after', text)
        self.assertNotIn('secret-private-resource', text)
        self.assertNotIn('APPLIED', text)
        self.assertIn('Recorded', text)

    async def test_no_undo_or_confirm_button_for_nonreversible_change(self):
        row = self.move()
        view = log.ChangeNotice(row)
        self.assertEqual([item.label for item in view.children], ['View details'])
        private = log.Undo(self.guild.id, self.owner.id, row)
        self.assertEqual([item.label for item in private.children], ['Close'])
        # Restart handlers still register both stable custom IDs for older messages.
        self.assertEqual(len(log.ChangeNotice().children), 2)

    async def test_private_group_pagination_is_owner_checked_and_never_undo(self):
        for index in range(10):
            self.move(8801 + index)
        root = self.deletion()
        await feed.flush(self.guild)
        request = self.notice_request(root)
        await log.ChangeNotice().review.callback(request)
        view = request.edit_original_response.call_args.kwargs['view']
        self.assertIn('Page 1 of 2', request.edit_original_response.call_args.kwargs['content'])
        self.assertNotIn('Undo', [item.label for item in view.children])
        denied = self.request(self.admin)
        await view.next_page.callback(denied)
        denied.response.edit_message.assert_not_awaited()
        next_request = self.request()
        await view.next_page.callback(next_request)
        self.assertIn('Page 2 of 2', next_request.response.edit_message.call_args.kwargs['content'])
        self.assertIn('private-chat-8810', next_request.response.edit_message.call_args.kwargs['content'])
        self.assertFalse(next_request.response.edit_message.call_args.kwargs['allowed_mentions'].everyone)

    async def test_history_selector_uses_names_and_readable_status(self):
        row = self.move()
        view = log.ChangeList(self.guild.id, self.owner.id, [row])
        option = view.children[0].options[0]
        self.assertIn('private-chat-8801', option.label)
        self.assertIn('Recorded', option.description)
        self.assertNotIn('discord:', option.label + option.description)

    async def test_private_mentions_are_escaped_and_permission_bits_not_dumped(self):
        row = self.record('role', 1234, 'observed_role_update',
                          {'name': '@everyone **old**', 'permissions': 0},
                          {'name': '@everyone **new**', 'permissions': 8, '_fields': ['name', 'permissions']})
        text = log.details(row, self.guild)
        self.assertNotIn('@everyone', text)
        self.assertIn('Access settings changed', text)
        self.assertNotIn('0 → 8', text)
        self.assertLessEqual(len(text), 1950)

    async def test_grouping_does_not_mutate_resource_or_managed_state(self):
        db.set_setting('runtime_structure:1:category:game', '{"name":"Intended name"}')
        self.move()
        self.deletion()
        await feed.flush(self.guild)
        self.assertEqual(db.get_setting('runtime_structure:1:category:game'), '{"name":"Intended name"}')
        self.guild.fetch_channels.assert_not_awaited()
        self.room.history.assert_not_called()
        self.room.delete.assert_not_called()
