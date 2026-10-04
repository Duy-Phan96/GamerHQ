"""Legacy owner Undo must not generate an inverse notification (offline)."""
import unittest
from unittest.mock import AsyncMock, patch

import test_server_change_observer as fixtures
from services import server_change_observer as observer
from services import structure_adoption_service as structure


class LegacyInverseTests(unittest.IsolatedAsyncioTestCase):
    channel = fixtures.ObserverTests.channel

    def setUp(self):
        fixtures.ObserverTests.setUp(self)
        observer._legacy_undos.clear()

    async def test_legacy_undo_event_does_not_create_an_inverse_notice(self):
        before, after = self.channel(), self.channel('after')
        change = structure.record_change(self.guild, 'channel', 'welcome', 801, None, 'channel_update',
            {'name': 'before', 'position': 1, 'category_id': None},
            {'name': 'after', 'position': 1, 'category_id': None}, reversible=True)
        await observer.observe(self.guild, 'channel', before, after, ignore={'name'})
        async def revert(*args):
            await observer.observe(self.guild, 'channel', after, before)
        with patch.object(structure, 'undo_change', AsyncMock(side_effect=revert)):
            await observer.undo_legacy(self.guild, self.owner, change['id'])
        self.assertEqual(len(structure.recent_changes(self.guild)), 1)
        self.assertEqual(observer._legacy_undos, {})
        # A later genuine reverse edit is still a new change.
        await observer.observe(self.guild, 'channel', before, after)
        self.assertEqual(len(structure.recent_changes(self.guild)), 2)

    async def test_failed_legacy_undo_does_not_suppress_a_later_manual_edit(self):
        before, after = self.channel(), self.channel('after')
        change = structure.record_change(self.guild, 'channel', 'welcome', 801, None, 'channel_update',
            {'name': 'before', 'position': 1, 'category_id': None},
            {'name': 'after', 'position': 1, 'category_id': None}, reversible=True)
        await observer.observe(self.guild, 'channel', before, after, ignore={'name'})
        with patch.object(structure, 'undo_change', AsyncMock(side_effect=ValueError('conflict'))):
            with self.assertRaises(ValueError):
                await observer.undo_legacy(self.guild, self.owner, change['id'])
        self.assertEqual(observer._legacy_undos, {})
        await observer.observe(self.guild, 'channel', after, before)
        self.assertEqual(len(structure.recent_changes(self.guild)), 2)
