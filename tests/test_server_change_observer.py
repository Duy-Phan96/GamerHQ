"""Gateway coverage and safe Undo in the existing owner history (offline only)."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from database import db
from services import owner_change_feed as feed
from services import server_change_observer as observer
from services import structure_adoption_service as structure


class Role(SimpleNamespace):
    def __ge__(self, other):
        return self.position >= other.position


class Channel(SimpleNamespace):
    pass


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = patch.object(db, 'DB_PATH', Path(temp.name) / 'observer.db')
        store.start()
        self.addCleanup(store.stop)
        db.init_db()
        observer._locks.clear()
        feed._locks.clear()
        flush = patch.object(feed, 'flush', AsyncMock())
        self.flush = flush.start()
        self.addCleanup(flush.stop)
        self.guild = SimpleNamespace(id=101, owner_id=701, name='Test server', description=None,
            channels=[], roles=[], features=[], me=SimpleNamespace(id=900),
            fetch_channel=AsyncMock(), fetch_roles=AsyncMock(), fetch_member=AsyncMock())
        self.owner = SimpleNamespace(id=701)
        self.client = SimpleNamespace(fetch_guild=AsyncMock(return_value=self.guild))

    def channel(self, name='before', **kwargs):
        values = dict(id=801, name=name, topic='old topic', guild=self.guild,
                      position=1, category_id=None, overwrites={}, edit=AsyncMock())
        values.update(kwargs)
        return Channel(**values)

    def role(self, name='before', **kwargs):
        values = dict(id=802, name=name, guild=self.guild, managed=False, position=10,
            permissions=discord.Permissions.none(), colour=discord.Colour(0), hoist=False,
            mentionable=False, unicode_emoji=None, icon=None, edit=AsyncMock())
        values.update(kwargs)
        return Role(**values)

    def latest(self):
        return structure.recent_changes(self.guild)[0]

    async def rename(self):
        await observer.observe(self.guild, 'channel', self.channel(), self.channel('after'))
        return self.latest()

    async def test_unmapped_channel_change_is_persisted_and_notified_immediately(self):
        change = await self.rename()
        self.assertEqual(change['action'], 'observed_channel_update')
        self.assertEqual(change['after']['_fields'], ['name'])
        self.assertTrue(feed.can_undo(change))
        self.assertIsNone(change['actor_id'])
        self.flush.assert_awaited_once_with(self.guild)
        self.assertIsNone(db.get_setting('runtime_structure:101:channel:discord:channel:801'))

    async def test_duplicate_gateway_delivery_and_restart_do_not_duplicate_history(self):
        await self.rename()
        observer._locks.clear()
        await self.rename()
        self.assertEqual(len(structure.recent_changes(self.guild)), 1)
        self.assertEqual(self.flush.await_count, 1)

    async def test_real_back_and_forth_edits_are_not_deduplicated_away(self):
        await self.rename()
        await observer.observe(self.guild, 'channel', self.channel('after'), self.channel())
        await self.rename()
        self.assertEqual(len(structure.recent_changes(self.guild)), 3)

    async def test_role_and_server_settings_changes_share_existing_history(self):
        await observer.observe(self.guild, 'role', self.role(), self.role('new role'))
        self.assertTrue(feed.can_undo(self.latest()))
        before = copy.copy(self.guild)
        after = copy.copy(self.guild)
        after.name = 'New server name'
        await observer.observe(after, 'guild', before, after)
        self.assertTrue(feed.can_undo(self.latest()))
        self.assertEqual(len(structure.recent_changes(self.guild)), 2)

    async def test_permission_changes_are_recorded_without_unsafe_undo(self):
        await observer.observe(self.guild, 'role', self.role(),
                               self.role(permissions=discord.Permissions(administrator=True)))
        self.assertFalse(feed.can_undo(self.latest()))
        self.assertIn('permissions', self.latest()['after']['_fields'])
        target = SimpleNamespace(id=201)
        # A real hashable overwrite target, without depending on a live member cache.
        class Target:
            id = target.id
        await observer.observe(self.guild, 'channel', self.channel(),
            self.channel(overwrites={Target(): discord.PermissionOverwrite(view_channel=True)}))
        self.assertFalse(feed.can_undo(self.latest()))
        self.assertIn('overwrites', self.latest()['after']['_fields'])

    async def test_create_delete_are_logged_but_pure_ordering_noise_is_quiet(self):
        resource = self.channel()
        await observer.observe(self.guild, 'channel', None, resource)
        self.assertFalse(feed.can_undo(self.latest()))
        await observer.observe(self.guild, 'channel', resource, None)
        self.assertFalse(feed.can_undo(self.latest()))
        count = len(structure.recent_changes(self.guild))
        await observer.observe(self.guild, 'channel', self.channel(), self.channel(position=3))
        self.assertEqual(len(structure.recent_changes(self.guild)), count)
        self.assertEqual(json.loads(db.get_setting(observer.key(101, 'channel', 801)))['position'], 3)

    async def test_parent_move_records_category_without_incidental_position(self):
        before = self.channel(position=1, category_id=10)
        after = self.channel(position=22, category_id=11)
        await observer.observe(self.guild, 'channel', before, after)
        change = self.latest()
        self.assertEqual(change['after']['_fields'], ['category_id'])
        detail = observer.detail_text(change)
        self.assertIn('moved to a different category', detail)
        self.assertNotIn('22', detail)

    async def test_private_values_do_not_leak_into_channel_notice(self):
        change = await self.rename()
        rendered = feed.render_notice(change)
        self.assertNotIn('old topic', rendered)
        self.assertNotIn('discord:channel:801', rendered)
        self.assertIn('Channel changed', rendered)
        self.assertIn('before', observer.detail_text(change))

    async def test_first_baseline_is_quiet_then_offline_net_change_is_recorded_once(self):
        channel = self.channel()
        self.guild.channels = [channel]
        await observer.reconcile(self.guild)
        self.assertEqual(structure.recent_changes(self.guild), [])
        channel.name = 'offline rename'
        await observer.reconcile(self.guild)
        self.assertEqual(self.latest()['action'], 'observed_channel_offline')
        self.assertFalse(feed.can_undo(self.latest()))
        await observer.reconcile(self.guild)
        self.assertEqual(len(structure.recent_changes(self.guild)), 1)

    async def test_offline_missing_resource_is_only_a_nonreversible_net_difference(self):
        self.guild.channels = [self.channel()]
        await observer.reconcile(self.guild)
        self.guild.channels = []
        await observer.reconcile(self.guild)
        self.assertEqual(self.latest()['action'], 'observed_channel_offline')
        self.assertFalse(self.latest()['reversible'])

    async def test_notification_error_does_not_lose_history_or_initial_backlog(self):
        self.flush.side_effect = RuntimeError('notification temporarily unavailable')
        with self.assertRaises(RuntimeError):
            await self.rename()
        self.assertEqual(len(structure.recent_changes(self.guild)), 1)
        self.assertLess(int(db.get_setting(feed.cursor_key(self.guild.id))), self.latest()['id'])
        self.assertIsNotNone(db.get_setting(observer.key(self.guild.id, 'channel', 801)))

    async def test_legacy_layout_is_not_logged_twice_but_other_fields_are_kept(self):
        before, after = self.channel(), self.channel('after', topic='new topic')
        mark = observer.history_mark(self.guild)
        structure.record_change(self.guild, 'channel', 'welcome', 801, None, 'channel_update',
            {'name': before.name, 'position': 1, 'category_id': None},
            {'name': after.name, 'position': 1, 'category_id': None}, reversible=True)
        covered = observer.covered_fields(self.guild, after, 'channel', mark)
        await observer.observe(self.guild, 'channel', before, after, ignore=covered)
        self.assertEqual(self.latest()['after']['_fields'], ['topic'])
        self.assertEqual(len(structure.recent_changes(self.guild)), 2)

    async def test_suppressed_bot_write_still_gets_an_owner_notice(self):
        from cogs.server_changes import ServerChanges
        before, after = self.channel(), self.channel('bot rename')
        with patch.object(discord, 'TextChannel', Channel), \
             patch.object(structure, 'channel_mapping', return_value='welcome'), \
             patch.object(structure, 'audit_actor', AsyncMock(return_value=900)), \
             patch.object(structure, 'observe_channel_update', AsyncMock(return_value={'permissions_changed': False})):
            await ServerChanges(SimpleNamespace()).on_guild_channel_update(before, after)
        self.assertEqual(self.latest()['after']['_fields'], ['name'])
        self.assertEqual(len(structure.recent_changes(self.guild)), 1)

    async def test_all_new_gateway_listeners_write_to_the_same_history(self):
        from cogs.server_changes import ServerChanges
        cog = ServerChanges(SimpleNamespace())
        await cog.on_guild_channel_create(self.channel())
        await cog.on_guild_role_create(self.role())
        await cog.on_guild_role_update(self.role(), self.role('other'))
        await cog.on_guild_role_delete(self.role('other'))
        after = copy.copy(self.guild)
        after.name = 'Other server'
        await cog.on_guild_update(self.guild, after)
        self.assertEqual(len(structure.recent_changes(self.guild)), 5)

    async def test_undo_fetches_current_state_and_preserves_unrelated_fields(self):
        change = await self.rename()
        live = self.channel('after', topic='new unrelated topic')
        verified = self.channel('before', topic='new unrelated topic')
        self.guild.fetch_channel.side_effect = [live, verified]
        with patch('services.channel_change_service.expect'):
            updated = await observer.undo(self.guild, self.owner, change['id'], self.client)
        self.assertEqual(updated['status'], 'UNDONE')
        self.assertEqual(updated['undone_by'], self.owner.id)
        self.assertEqual(live.edit.call_args.kwargs,
            {'name': 'before', 'reason': f"GamerHQ owner undo change #{change['id']}"})
        self.assertEqual(json.loads(db.get_setting(observer.key(101, 'channel', 801)))['topic'], 'new unrelated topic')
        await observer.observe(self.guild, 'channel', live, verified)
        self.assertEqual(len(structure.recent_changes(self.guild)), 1)

    async def test_stale_undo_refuses_without_editing(self):
        change = await self.rename()
        live = self.channel('newer rename')
        self.guild.fetch_channel.return_value = live
        with self.assertRaisesRegex(ValueError, 'changed again'):
            await observer.undo(self.guild, self.owner, change['id'], self.client)
        live.edit.assert_not_awaited()
        self.assertEqual(self.latest()['status'], 'APPLIED')

    async def test_nonowner_and_changed_ownership_are_rejected(self):
        change = await self.rename()
        with self.assertRaises(ValueError):
            await observer.undo(self.guild, SimpleNamespace(id=999), change['id'], self.client)
        self.client.fetch_guild.assert_not_awaited()
        self.client.fetch_guild.return_value = SimpleNamespace(owner_id=999)
        with self.assertRaisesRegex(ValueError, 'ownership changed'):
            await observer.undo(self.guild, self.owner, change['id'], self.client)
        self.guild.fetch_channel.assert_not_awaited()

    async def test_concurrent_undo_executes_only_once(self):
        change = await self.rename()
        live, verified = self.channel('after'), self.channel()
        self.guild.fetch_channel.side_effect = [live, verified]
        with patch('services.channel_change_service.expect'):
            results = await asyncio.gather(
                observer.undo(self.guild, self.owner, change['id'], self.client),
                observer.undo(self.guild, self.owner, change['id'], self.client), return_exceptions=True)
        live.edit.assert_awaited_once()
        self.assertEqual(sum(isinstance(item, ValueError) for item in results), 1)

    async def test_failed_or_partial_undo_is_never_marked_successful(self):
        change = await self.rename()
        live = self.channel('after')
        self.guild.fetch_channel.return_value = live
        with patch('services.channel_change_service.expect'):
            with self.assertRaisesRegex(ValueError, 'did not confirm'):
                await observer.undo(self.guild, self.owner, change['id'], self.client)
        self.assertEqual(self.latest()['status'], 'REVIEW_REQUIRED')
        self.assertFalse(feed.can_undo(self.latest()))

    async def test_missing_discord_permissions_are_not_reported_as_success(self):
        change = await self.rename()
        live = self.channel('after')
        live.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'missing rights')
        self.guild.fetch_channel.return_value = live
        with patch('services.channel_change_service.expect'):
            with self.assertRaises(ValueError):
                await observer.undo(self.guild, self.owner, change['id'], self.client)
        self.assertEqual(self.latest()['status'], 'REVIEW_REQUIRED')

    async def test_role_undo_checks_bot_hierarchy(self):
        await observer.observe(self.guild, 'role', self.role(), self.role('after'))
        change = self.latest()
        live = self.role('after')
        self.guild.fetch_roles.return_value = [live]
        self.guild.fetch_member.return_value = SimpleNamespace(
            guild_permissions=discord.Permissions(manage_roles=True), top_role=self.role(position=1))
        with self.assertRaisesRegex(ValueError, 'higher role'):
            await observer.undo(self.guild, self.owner, change['id'], self.client)
        live.edit.assert_not_awaited()

    async def test_interrupted_undo_requires_review_after_restart(self):
        change = await self.rename()
        with db.connect() as conn:
            conn.execute("UPDATE structure_change_log SET status='UNDOING' WHERE id=?", (change['id'],))
        await observer.reconcile(self.guild)
        self.assertEqual(structure.get_change(self.guild, change['id'])['status'], 'REVIEW_REQUIRED')

    async def test_unavailable_guild_does_not_create_false_offline_deletions(self):
        self.guild.channels = [self.channel()]
        await observer.reconcile(self.guild)
        self.guild.channels = []
        self.guild.unavailable = True
        await observer.reconcile(self.guild)
        self.assertEqual(structure.recent_changes(self.guild), [])

    async def test_confirmed_mapped_name_undo_keeps_intended_state_consistent(self):
        db.set_setting('managed_channel:101:welcome', 801)
        structure.save_runtime_state(self.guild, 'channel', 'welcome',
            {'id': 801, 'name': 'after', 'position': 7, 'category_id': None, 'kind': 'text'})
        change = await self.rename()
        live, verified = self.channel('after'), self.channel()
        self.guild.fetch_channel.side_effect = [live, verified]
        with patch('services.channel_change_service.expect'):
            await observer.undo(self.guild, self.owner, change['id'], self.client)
        intended = structure.runtime_state(self.guild, 'channel', 'welcome')
        self.assertEqual(intended['name'], 'before')
        self.assertEqual(intended['position'], 7)

    async def test_role_rename_undo_rechecks_hierarchy_and_verifies_the_result(self):
        await observer.observe(self.guild, 'role', self.role(), self.role('after'))
        change = self.latest()
        live, verified = self.role('after'), self.role()
        self.guild.fetch_roles.side_effect = [[live], [verified]]
        self.guild.fetch_member.return_value = SimpleNamespace(
            guild_permissions=discord.Permissions(manage_roles=True), top_role=self.role(position=20))
        updated = await observer.undo(self.guild, self.owner, change['id'], self.client)
        self.assertEqual(updated['status'], 'UNDONE')
        self.assertEqual(live.edit.call_args.kwargs['name'], 'before')

    async def test_large_private_detail_still_explains_undo_limitations(self):
        await observer.observe(self.guild, 'role', self.role(), self.role(permissions=discord.Permissions.all()))
        change = self.latest()
        change['after']['_fields'] = [f'field-{n}' for n in range(20)]
        for field in change['after']['_fields']:
            change['before'][field], change['after'][field] = 'a' * 500, 'b' * 500
        rendered = observer.detail_text(change)
        self.assertLessEqual(len(rendered), 1950)
        self.assertIn('Undo unavailable', rendered)
