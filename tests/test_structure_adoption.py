"""Runtime Discord-structure adoption and owner undo regressions."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

import discord
import test_onboarding as fixtures

from database import db
from services import server_setup_service as setup
from services import community_structure_service as community
from services import structure_adoption_service as runtime
from services import managed_message_service as managed
from services.server_service import upsert_fixed_message
from cogs import owner_changelog


def clone_channel(channel):
    result = copy.copy(channel)
    result.overwrites = {
        target: discord.PermissionOverwrite.from_pair(*value.pair())
        for target, value in channel.overwrites.items()
    }
    return result


class StructureAdoptionTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    async def asyncSetUp(self):
        self.guild.owner_id = 71
        self.owner = MagicMock(spec=discord.Member)
        self.owner.id = 71
        self.owner.bot = False
        self.owner.guild_permissions = discord.Permissions(administrator=True)
        self.guild.members = [self.owner]
        self.guild.me.guild_permissions = discord.Permissions(view_audit_log=False)
        runtime._locks.clear()
        runtime._expected_deletes.clear()
        await setup.repair_server(self.guild, self.bot)

    async def test_mapped_channel_rename_is_adopted_repair_keeps_it_and_owner_can_undo(self):
        channel = community.core_channel(self.guild, 'looking-for-group')
        original = channel.name
        before = clone_channel(channel)
        channel.name = '🎯・find-a-game'

        await runtime.observe_channel_update(before, channel, actor_id=self.owner.id)
        state = runtime.channel_state(self.guild, 'looking-for-group', channel_id=channel.id)
        self.assertEqual(state['name'], '🎯・find-a-game')
        self.assertEqual(db.get_setting(f'managed_channel:{self.guild.id}:looking-for-group'), str(channel.id))

        await setup.repair_server(self.guild, self.bot)
        self.assertEqual(channel.name, '🎯・find-a-game')

        change = runtime.recent_changes(self.guild, 1)[0]
        self.assertTrue(change['reversible'])
        await runtime.undo_change(self.guild, self.owner, change['id'])
        self.assertEqual(channel.name, original)
        self.assertEqual(runtime.get_change(self.guild, change['id'])['status'], 'UNDONE')

    async def test_incidental_channel_position_shift_updates_state_without_owner_notice(self):
        channel = community.core_channel(self.guild, 'looking-for-group')
        before = clone_channel(channel)
        channel.position += 8
        count = len(runtime.recent_changes(self.guild))
        await runtime.observe_channel_update(before, channel, actor_id=self.owner.id)
        self.assertEqual(len(runtime.recent_changes(self.guild)), count)
        self.assertEqual(runtime.channel_state(self.guild, 'looking-for-group')['position'], channel.position)

    async def test_category_position_shift_updates_state_without_owner_notice(self):
        games = community.core_category(self.guild, 'games')
        before = copy.copy(games)
        before.overwrites = dict(games.overwrites)
        games.position = getattr(games, 'position', 0) + 4
        count = len(runtime.recent_changes(self.guild))
        await runtime.observe_category_update(before, games, actor_id=self.owner.id)
        self.assertEqual(len(runtime.recent_changes(self.guild)), count)
        self.assertEqual(runtime.category_state(self.guild, 'games', category_id=games.id)['position'], games.position)

    async def test_child_detach_from_deleted_parent_is_not_separate_owner_change(self):
        channel = community.core_channel(self.guild, 'looking-for-group')
        before = clone_channel(channel)
        old_parent = channel.category
        if old_parent in self.guild.categories:
            self.guild.categories.remove(old_parent)
        if old_parent in self.guild.channels:
            self.guild.channels.remove(old_parent)
        channel.category = None
        count = len(runtime.recent_changes(self.guild))
        await runtime.observe_channel_update(before, channel, actor_id=self.owner.id)
        self.assertEqual(len(runtime.recent_changes(self.guild)), count)
        self.assertIsNone(runtime.channel_state(self.guild, 'looking-for-group')['category_id'])

    async def test_public_move_is_adopted_and_restart_bootstrap_does_not_restore_old_parent(self):
        channel = community.core_channel(self.guild, 'looking-for-group')
        before = clone_channel(channel)
        channel.category = self.community

        await runtime.observe_channel_update(before, channel, actor_id=self.owner.id)
        self.assertEqual(runtime.channel_state(self.guild, 'looking-for-group')['category_id'], self.community.id)

        runtime._locks.clear()
        await runtime.bootstrap(self.guild)
        await setup.repair_server(self.guild, self.bot)
        self.assertIs(channel.category, self.community)

    async def test_intentionally_deleted_optional_channel_stays_removed_after_repair(self):
        channel = community.core_channel(self.guild, 'suggestions')
        self.assertIsNotNone(channel)
        self.guild.text_channels.remove(channel)

        with patch('services.structure_adoption_service.asyncio.sleep', new=AsyncMock()):
            self.assertTrue(await runtime.observe_channel_delete(channel, actor_id=self.owner.id))

        self.assertEqual(db.get_setting(f'managed_channel_removed:{self.guild.id}:suggestions'), '1')
        self.assertIsNone(community.core_channel(self.guild, 'suggestions'))
        count = len(self.guild.text_channels)
        await setup.repair_server(self.guild, self.bot)
        self.assertEqual(len(self.guild.text_channels), count)
        self.assertIsNone(community.core_channel(self.guild, 'suggestions'))

    async def test_category_rename_is_identity_preserving_and_reversible(self):
        games = community.core_category(self.guild, 'games')
        before = copy.copy(games)
        before.overwrites = dict(games.overwrites)
        games.name = '🎮 PLAY ZONE'

        await runtime.observe_category_update(before, games, actor_id=self.owner.id)
        state = runtime.category_state(self.guild, 'games', category_id=games.id)
        self.assertEqual(state['name'], '🎮 PLAY ZONE')
        change = runtime.recent_changes(self.guild, 1)[0]
        await runtime.undo_change(self.guild, self.owner, change['id'])
        self.assertEqual(games.name, before.name)

    async def test_managed_message_delete_retires_and_refresh_does_not_repost(self):
        channel = community.core_channel(self.guild, 'guide')
        message = fixtures.FakeMessage(channel, 7777, 'Managed guide', author=self.guild.me.id, pinned=True)
        channel.messages[message.id] = message
        key = 'test_managed_board'
        state = {
            'key': key, 'guild_id': self.guild.id, 'channel_id': channel.id,
            'message_id': message.id, 'label': 'Test Board', 'content': message.content,
            'buttons': [], 'default_content': message.content, 'default_buttons': [],
            'customized': False, 'version': 1, 'content_hash': managed.digest(message.content),
            'pending': False,
        }
        managed.store(state)
        db.set_setting(key, message.id)
        sends = channel.sends

        with patch('services.structure_adoption_service.asyncio.sleep', new=AsyncMock()):
            self.assertTrue(await runtime.observe_managed_message_delete(self.guild, channel.id, message.id))

        retired = managed.load(key)
        self.assertTrue(retired['retired'])
        self.assertEqual(db.get_setting(key), '')
        self.assertIsNone(await upsert_fixed_message(channel, setting_key=key, content='Managed guide'))
        self.assertEqual(channel.sends, sends)

    async def test_owner_log_can_restore_deleted_optional_channel_as_replacement(self):
        channel = community.core_channel(self.guild, 'suggestions')
        old_id = channel.id
        self.guild.text_channels.remove(channel)
        with patch('services.structure_adoption_service.asyncio.sleep', new=AsyncMock()):
            self.assertTrue(await runtime.observe_channel_delete(channel, actor_id=self.owner.id))
        change = runtime.recent_changes(self.guild, 1)[0]
        self.assertEqual(change['action'], 'channel_delete')

        from services import resource_restore_service as restore
        draft = restore.preview(self.guild, self.owner, 'suggestions')
        replacement = await restore.restore(self.guild, self.owner, draft)
        self.assertNotEqual(replacement.id, old_id)
        self.assertEqual(db.get_setting(f'managed_channel_removed:{self.guild.id}:suggestions'), '0')
        self.assertEqual(db.get_setting(f'managed_channel:{self.guild.id}:suggestions'), str(replacement.id))

    def test_owner_change_log_permissions_deny_normal_staff(self):
        rights = owner_changelog.owner_rights(self.guild)
        self.assertFalse(rights[self.guild.default_role].view_channel)
        self.assertNotIn(self.guild.mod, rights)  # @everyone deny; no per-role grant or redundant deny.
        self.assertEqual(len(rights), 3)
        self.assertTrue(rights[self.owner].view_channel)
        self.assertTrue(rights[self.guild.me].view_channel)


if __name__ == '__main__':
    unittest.main()
