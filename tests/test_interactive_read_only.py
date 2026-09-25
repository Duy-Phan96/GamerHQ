"""Offline contracts for interactive boards and retryable community-event repair."""
import unittest
import copy
from unittest.mock import patch
import discord
import test_onboarding as fixtures
from database import db
from services import onboarding_service as policy, community_structure_service as structure
from services import server_setup_service as setup, health_service as health
from services import managed_message_service as managed
from services.server_service import ServerMessageError


class InteractiveBoardTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    def assert_interactive(self, channel):
        rights = channel.overwrites_for(self.guild.default_role)
        for bit in ('view_channel', 'read_message_history', 'add_reactions', 'use_application_commands'):
            self.assertIs(getattr(rights, bit), True, bit)
        for bit in ('send_messages', 'send_messages_in_threads', 'create_public_threads', 'create_private_threads'):
            self.assertIs(getattr(rights, bit), False, bit)
        self.assertTrue(channel.overwrites_for(self.guild.mod).send_messages)
        self.assertTrue(channel.overwrites_for(self.guild.me).send_messages)

    async def test_selectors_repair_denies_without_changing_unrelated_bits(self):
        self.start.overwrites[self.guild.default_role] = discord.PermissionOverwrite(add_reactions=False, use_application_commands=False)
        for name in ('choose-your-games', 'choose-your-roles'):
            board = structure.core_channel(self.guild, name)
            board.overwrites = dict(self.start.overwrites)
            board.overwrites[self.guild.custom] = discord.PermissionOverwrite(add_reactions=False, use_application_commands=False, attach_files=False, view_channel=False)
            await policy.set_read_only(board)
            self.assert_interactive(board)
            rights = board.overwrites_for(self.guild.custom)
            self.assertTrue(rights.add_reactions and rights.use_application_commands)
            self.assertIs(rights.attach_files, False)
            self.assertIs(rights.view_channel, False)
            edits = len(board.edits)
            await policy.set_read_only(board)
            self.assertEqual(len(board.edits), edits)
        self.assertFalse(self.start.overwrites[self.guild.default_role].add_reactions)

    async def test_static_and_mapped_interactive_classification(self):
        board = structure.core_channel(self.guild, 'rules')
        await policy.set_read_only(board)
        self.assertIs(board.overwrites_for(self.guild.default_role).add_reactions, False)
        self.assertIsNone(board.overwrites_for(self.guild.default_role).use_application_commands)
        db.set_setting(structure.resource_key(self.guild, 'choose-your-games'), board.id)
        await policy.set_read_only(board)
        self.assert_interactive(board)
        self.assertIsNone(board.overwrites_for(self.guild.default_role).use_external_emojis)

    async def test_event_creation_order_history_pins_and_repeat_repair(self):
        tournament = structure.core_channel(self.guild, 'tournaments')
        giveaway = structure.core_channel(self.guild, 'giveaways')
        custom = discord.PermissionOverwrite(attach_files=True, view_channel=False)
        tournament.overwrites[self.guild.custom] = custom
        history = self.add_message(giveaway, 'Existing giveaway', author=20, pinned=True)
        _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertFalse(failed)
        event = structure.core_channel(self.guild, 'community-events')
        self.assert_interactive(event)
        self.assertEqual(policy.alias(event.category.name), 'events')
        ordered = sorted(event.category.text_channels, key=lambda c: (c.position, c.id))
        self.assertEqual([c.id for c in ordered], [event.id, tournament.id, giveaway.id])
        self.assertEqual(tournament.overwrites, {self.guild.custom: custom})
        self.assertFalse(history.deleted)
        self.assertEqual(event.sends, 1)
        message = next(iter(event.messages.values()))
        self.assertTrue(message.pinned)
        self.assertEqual(message.content, structure.EVENTS_INTRO)
        self.assertIn(f'community_events:{self.guild.id}', managed.specs(self.guild))
        ids = [c.id for c in self.guild.channels]
        position_updates = len(self.guild.position_updates)
        db.set_setting(f'community_events:{self.guild.id}', '')
        await setup.repair_server(self.guild, self.bot)
        self.assertEqual(ids, [c.id for c in self.guild.channels])
        self.assertEqual(len(self.guild.position_updates), position_updates)
        self.assertEqual(event.sends, 1)

    async def test_event_reuses_stored_id_when_renamed_and_moved(self):
        event = self.guild.add_channel('custom-event-name', self.community)
        db.set_setting(structure.resource_key(self.guild, 'community-events'), event.id)
        history = self.add_message(event, 'Keep this', author=20)
        await setup.repair_server(self.guild, self.bot)
        self.assertIs(structure.core_channel(self.guild, 'community-events'), event)
        self.assertEqual(policy.alias(event.category.name), 'events')
        self.assert_interactive(event)
        self.assertFalse(history.deleted)

    async def test_event_ambiguity_and_private_placement_fail_closed(self):
        event = self.guild.add_channel('community-events', self.staff)
        changed, failed = [], []
        with self.assertRaises(ServerMessageError):
            await structure.migrate_boards(self.guild, changed, failed)
        db.set_setting(structure.resource_key(self.guild, 'community-events'), event.id)
        with self.assertRaises(ServerMessageError):
            await structure.migrate_boards(self.guild, changed, failed)
        self.assertEqual(event.edits, [])
        self.assertEqual(event.messages, {})
        db.set_setting(structure.resource_key(self.guild, 'community-events'), '')
        event.category = self.community
        event.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        with self.assertRaises(ServerMessageError):
            await structure.migrate_boards(self.guild, changed, failed)
        self.assertEqual(event.edits, [])

    async def test_named_event_reused_and_retry_after_pin_failure_keeps_id(self):
        event = self.guild.add_channel('🎉・community-events', self.start)
        with patch.object(structure, 'refresh_boards', side_effect=ServerMessageError('Retry pin delivery')):
            _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertTrue(any('Retry pin' in text for text in failed))
        self.assertEqual(db.get_setting(structure.resource_key(self.guild, 'community-events')), str(event.id))
        await setup.repair_server(self.guild, self.bot)
        self.assertIs(structure.core_channel(self.guild, 'community-events'), event)
        self.assertEqual(sum(policy.alias(c.name) == 'community-events' for c in self.guild.text_channels), 1)
        self.assertEqual(event.sends, 1)

    async def test_conflicting_event_names_never_merge_or_create(self):
        event = self.guild.add_channel('renamed', self.start)
        db.set_setting(structure.resource_key(self.guild, 'community-events'), event.id)
        self.guild.add_channel('community-events', self.community)
        ids = [c.id for c in self.guild.channels]
        with self.assertRaises(ServerMessageError):
            await structure.migrate_boards(self.guild, [], [])
        self.assertEqual(ids, [c.id for c in self.guild.channels])

    async def test_health_detects_reaction_drift_readonly_then_repair_fixes_it(self):
        await setup.repair_server(self.guild, self.bot)
        boards = [structure.core_channel(self.guild, name) for name in ('choose-your-games', 'choose-your-roles', 'community-events')]
        for board in boards:
            board.overwrites[self.guild.default_role].add_reactions = False
        with db.connect() as conn:
            before = list(map(tuple, conn.execute('SELECT * FROM settings')))
        findings = await health.scan(self.guild, messages=False)
        for name in ('choose-your-games', 'choose-your-roles', 'community-events'):
            finding = next(f for f in findings if f.name == name + ' read-only')
            self.assertEqual(finding.state, 'REPAIRABLE')
            self.assertIn('add_reactions', finding.detail)
        with db.connect() as conn:
            self.assertEqual(before, list(map(tuple, conn.execute('SELECT * FROM settings'))))
        await setup.repair_server(self.guild, self.bot)
        findings = await health.scan(self.guild, messages=False)
        for name in ('choose-your-games', 'choose-your-roles', 'community-events'):
            self.assertEqual(next(f.state for f in findings if f.name == name + ' read-only'), 'PASS')

    async def test_event_order_keeps_unknown_channels_relative_order(self):
        await setup.repair_server(self.guild, self.bot)
        event = structure.core_channel(self.guild, 'community-events')
        extra1 = self.guild.add_channel('custom-one', event.category)
        extra2 = self.guild.add_channel('custom-two', event.category)
        event.position = 500
        current, ordered = structure.event_order(self.guild, await self.guild.fetch_channels())
        self.assertEqual([c.id for c in ordered if c in (extra1, extra2)], [extra1.id, extra2.id])
        self.assertEqual([c.id for c in ordered if c not in (extra1, extra2)],
                         [structure.core_channel(self.guild, n).id for n in structure.EVENT_BOARDS])

    async def test_event_order_uses_fresh_placement_before_gateway_cache_catches_up(self):
        events = self.guild.add_category('🏆 EVENTS')
        snapshots = []
        for i, name in enumerate(structure.EVENT_BOARDS):
            cached = structure.core_channel(self.guild, name) or self.guild.add_channel(name, self.start)
            db.set_setting(structure.resource_key(self.guild, name), cached.id)
            fresh = copy.copy(cached)
            fresh.category = events
            fresh.position = 20 if i == 0 else i
            snapshots.append(fresh)
        # Discord channel equality compares IDs; our simple fixtures use identity.
        # Include the same fresh objects in the text inventory while get_channel
        # continues resolving the older objects first, as after a move.
        self.guild.text_channels.extend(snapshots)
        current, ordered = structure.event_order(self.guild, snapshots)
        self.assertEqual([c.id for c in ordered], [c.id for c in snapshots])
        self.assertNotEqual([c.id for c in current], [c.id for c in ordered])
