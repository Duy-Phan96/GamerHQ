"""Batch Game System V3 migration and adoption regressions."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import test_game_channels_v2 as game_fixtures

from database import db
from services import game_channel_service as channels
from services import game_system_migration as migration
from services import structure_adoption_service as runtime


class GameSystemMigrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = game_fixtures.ChannelTests.setUp
    make_game = game_fixtures.ChannelTests.make_game

    async def asyncSetUp(self):
        await game_fixtures.ChannelTests.asyncSetUp(self)
        runtime._expected_deletes.clear()
        runtime._locks.clear()

    create = game_fixtures.ChannelTests.create

    async def legacy(self, game, *, unknown=False):
        category = self.guild.add_category(game['name'].upper())
        chat = self.guild.add_channel('chat', category)
        chat.messages[5000 + game['id']] = SimpleNamespace(content=f"history-{game['id']}")
        lfg = self.guild.add_channel('lfg', category)
        if unknown:
            self.guild.add_channel('custom-user-channel', category)
        with db.connect() as conn:
            conn.execute(
                'UPDATE games SET category_id=?,chat_channel_id=?,lfg_channel_id=?,area_enabled=1 WHERE id=?',
                (category.id, chat.id, lfg.id, game['id']),
            )
        return category, chat, lfg

    async def test_batch_migration_moves_existing_chats_preserving_ids_history_roles_and_selection(self):
        first_category, first_chat, _ = await self.legacy(self.game)
        second = await self.make_game('Warframe')
        second_category, second_chat, _ = await self.legacy(second)
        first_id, second_id = first_chat.id, second_chat.id
        first_role, second_role = self.game['role_id'], second['role_id']

        draft = migration.preview(self.guild, self.actor)
        self.assertEqual({plan['game']['id'] for plan in draft['plans']}, {self.game['id'], second['id']})
        moved, skipped = await migration.apply(self.guild, self.actor, draft)
        self.assertEqual(skipped, [])
        self.assertEqual(set(moved), {'Terraria', 'Warframe'})

        first = db.get_game_by_id(self.game['id'])
        second = db.get_game_by_id(second['id'])
        self.assertEqual((first['channel_id'], second['channel_id']), (first_id, second_id))
        self.assertEqual((first['role_id'], second['role_id']), (first_role, second_role))
        self.assertTrue(first['selectable'] and second['selectable'])
        self.assertIs(first_chat.category, self.gaming)
        self.assertIs(second_chat.category, self.gaming)
        self.assertEqual(first_chat.messages[5000 + self.game['id']].content, f"history-{self.game['id']}")
        self.assertEqual(second_chat.messages[5000 + second['id']].content, f"history-{second['id']}")
        self.assertIn(first_category, self.guild.categories)
        self.assertIn(second_category, self.guild.categories)

    async def test_cleanup_refuses_unknown_children_then_deletes_only_known_legacy_area(self):
        category, chat, lfg = await self.legacy(self.game, unknown=True)
        await migration.apply(self.guild, self.actor, migration.preview(self.guild, self.actor))

        blocked = migration.cleanup_preview(self.guild, self.actor)
        self.assertFalse(blocked['candidates'])
        self.assertTrue(any('unknown child channel' in row for row in blocked['review']))

        unknown = next(c for c in self.guild.text_channels if c.category is category and c.name == 'custom-user-channel')
        self.guild.text_channels.remove(unknown)
        draft = migration.cleanup_preview(self.guild, self.actor)
        self.assertEqual(len(draft['candidates']), 1)
        removed = await migration.cleanup(self.guild, self.actor, draft)
        self.assertEqual(removed, [self.game['name']])
        self.assertNotIn(category, self.guild.categories)
        self.assertNotIn(lfg, self.guild.text_channels)
        self.assertIn(chat, self.guild.text_channels)
        self.assertIs(chat.category, self.gaming)

    async def test_manual_game_channel_delete_preserves_game_role_and_selection_and_suppresses_threshold_recreate(self):
        channel = await channels.apply(
            self.guild, self.actor, channels.preview(self.guild, self.actor, self.game['id'])
        )
        original_role = self.game['role_id']
        self.guild.text_channels.remove(channel)

        with patch('services.structure_adoption_service.asyncio.sleep', new=AsyncMock()):
            self.assertTrue(await runtime.observe_channel_delete(channel, actor_id=self.actor.id))

        game = db.get_game_by_id(self.game['id'])
        self.assertIsNone(game['channel_id'])
        self.assertEqual(game['role_id'], original_role)
        self.assertTrue(game['selectable'])
        self.assertEqual(db.get_setting(f'game_channel_removed:{self.guild.id}:{game["id"]}'), '1')

        self.logs.send.reset_mock()
        await channels.check_threshold(self.guild, game['id'], count=20)
        self.logs.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
