"""Shared parent does not transfer ownership of individual Skill resources."""
import json
import unittest
from unittest.mock import AsyncMock

import discord
import test_onboarding as fixtures

from database import db
from services import server_operations as ops, shared_event_category as boundary
from services import community_structure_service as structure
from services.server_service import ServerMessageError


class SharedEventCategoryTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    def bind(self):
        events = self.guild.add_category('🏆 EVENTS')
        events.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=True)
        db.set_setting('managed_category:1:events', events.id)
        db.set_setting('skill_resources:1:event-manager', json.dumps({'categoryId': events.id, 'allowPublic': True}))
        with db.connect() as conn:
            conn.execute('INSERT INTO skill_guild_state VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (1, 'event-manager', 1, 1, 1, '0.1.3', 0))
        return events

    async def test_shared_parent_reused_and_core_boards_not_delegated(self):
        events = self.bind()
        board = self.guild.add_channel('📅・create-event', events)
        db.set_setting('managed_channel:1:create-event', board.id)
        rows = await ops._scan(self.guild, self.bot, message_keys=())
        by_name = {row['name']: row for row in rows}
        self.assertTrue(by_name['events']['shared_event_parent'])
        self.assertFalse(ops.needs_repair(self.guild, by_name['events']))
        self.assertEqual(by_name['create-event']['status'], 'EXACT_MATCH')
        self.assertFalse(by_name['create-event'].get('skill_protected', False))
        self.assertEqual(by_name['community-events']['status'], 'MISSING')
        self.assertFalse(boundary.blocked_destination(self.guild, board, {events.id},
                         shared=events.id, owned=set()))

    async def test_skill_owned_collision_is_never_adopted_or_reordered(self):
        events = self.bind()
        board = self.guild.add_channel('community-events', events)
        board.topic = 'skill-resource:private-fixture'
        with db.connect() as conn:
            conn.execute('INSERT INTO skill_managed_resources VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (1, 'event-manager', 'event:private', 'text', json.dumps({'channelId': board.id}), None, 0))
        rows = await ops._scan(self.guild, self.bot, message_keys=())
        row = next(r for r in rows if r['name'] == 'community-events')
        self.assertTrue(row['skill_protected'])
        self.assertEqual(row['safe'], [])
        with self.assertRaisesRegex(ServerMessageError, 'Skill-owned'):
            structure.core_channel(self.guild, 'community-events')
        self.assertTrue(boundary.blocked_destination(self.guild, board, {events.id, board.id},
                         shared=events.id, owned={board.id}))
        self.assertFalse(board.edits or board.sends)

    async def test_other_skill_or_private_parent_removes_exception(self):
        events = self.bind()
        db.set_setting('skill_resources:1:other', json.dumps({'categoryId': events.id}))
        self.assertIsNone(boundary.shared_id(self.guild))
        rows = await ops._scan(self.guild, self.bot, message_keys=())
        self.assertTrue(next(r for r in rows if r['name'] == 'events')['skill_protected'])
        db.set_setting('skill_resources:1:other', '{}')
        events.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        self.assertIsNone(boundary.shared_id(self.guild))
