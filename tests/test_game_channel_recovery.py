"""Explicit recovery of stale/legacy game chat mappings; no live Discord access."""
import copy
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import test_game_visibility as fixtures
from cogs import game_visibility_selector as ui
from database import db
from services import game_channel_recovery_service as recovery
from services import game_channel_service as channels
from services import game_readiness_service as readiness


class GameChannelRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.VisibilityTests.setUp
    asyncSetUp = fixtures.VisibilityTests.asyncSetUp
    make_game = fixtures.VisibilityTests.make_game
    create = fixtures.VisibilityTests.create
    legacy = fixtures.VisibilityTests.legacy
    set_visible = fixtures.VisibilityTests.set_visible

    def request(self):
        return SimpleNamespace(
            guild=self.guild, user=self.actor,
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(),
                                     defer=AsyncMock(), is_done=lambda: False),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def drop_channel(self, channel):
        if channel in self.guild.text_channels:
            self.guild.text_channels.remove(channel)

    async def test_deleted_canonical_mapping_can_be_cleared_and_recreated(self):
        old = await self.set_visible(True)
        old_id = old.id
        self.drop_channel(old)
        draft = await recovery.preview(self.guild, self.actor, self.game['id'])
        self.assertEqual(draft['candidates'], [])
        self.assertIn(old_id, draft['stale_ids'])
        self.assertTrue(draft['allow_new'])

        new = await recovery.apply(self.guild, self.actor, draft, 'new')
        self.assertNotEqual(new.id, old_id)
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], new.id)
        self.assertEqual(new.category_id, channels.category(self.guild).id)
        self.assertEqual(self.guild.create_text_channel.await_count, 2)

    async def test_stale_canonical_with_existing_legacy_chat_offers_move_and_preserves_history(self):
        _, legacy_chat, other = await self.legacy(detached=True)
        legacy_chat.messages[77] = SimpleNamespace(content='keep this')
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (999999, self.game['id']))
        draft = await recovery.preview(self.guild, self.actor, self.game['id'])
        self.assertEqual([c['id'] for c in draft['candidates']], [legacy_chat.id])
        self.assertIn(999999, draft['stale_ids'])

        moved = await recovery.apply(self.guild, self.actor, draft, legacy_chat.id)
        self.assertEqual(moved.id, legacy_chat.id)
        self.assertEqual(moved.messages[77].content, 'keep this')
        self.assertEqual(moved.category_id, channels.category(self.guild).id)
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], legacy_chat.id)
        self.assertIsNone(other.category_id)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_two_saved_existing_chats_require_explicit_choice(self):
        one = self.guild.add_channel('old-one', None)
        two = self.guild.add_channel('old-two', None)
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=?,chat_channel_id=? WHERE id=?',
                         (one.id, two.id, self.game['id']))
        draft = await recovery.preview(self.guild, self.actor, self.game['id'])
        self.assertEqual({c['id'] for c in draft['candidates']}, {one.id, two.id})
        chosen = await recovery.apply(self.guild, self.actor, draft, two.id)
        self.assertEqual(chosen.id, two.id)
        self.assertIn(one, self.guild.text_channels)
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], two.id)

    async def test_create_new_instead_never_deletes_existing_candidate(self):
        old = self.guild.add_channel('old-chat', None)
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (old.id, self.game['id']))
        draft = await recovery.preview(self.guild, self.actor, self.game['id'])
        new = await recovery.apply(self.guild, self.actor, draft, 'new')
        self.assertIn(old, self.guild.text_channels)
        self.assertNotEqual(new.id, old.id)
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], new.id)

    async def test_non_text_saved_resource_is_not_cleared_automatically(self):
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (self.gaming.id, self.game['id']))
        with self.assertRaisesRegex(ValueError, 'non-text'):
            await recovery.preview(self.guild, self.actor, self.game['id'])
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], self.gaming.id)

    async def test_unverified_creation_reservation_stays_blocked(self):
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (999999, self.game['id']))
        db.set_setting(f'game_channel_creation:{self.guild.id}:{self.game["id"]}', 'reserved')
        with self.assertRaisesRegex(ValueError, 'unverified result'):
            await recovery.preview(self.guild, self.actor, self.game['id'])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_other_game_reference_blocks_candidate_reuse(self):
        old = self.guild.add_channel('shared-old-chat', None)
        other = await self.make_game('Other Game')
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id IN (?,?)',
                         (old.id, self.game['id'], other['id']))
        with self.assertRaisesRegex(ValueError, 'another game'):
            await recovery.preview(self.guild, self.actor, self.game['id'])

    async def test_preview_and_cancel_are_read_only(self):
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (999999, self.game['id']))
        before = copy.deepcopy(db.get_game_by_id(self.game['id']))
        draft = await recovery.preview(self.guild, self.actor, self.game['id'])
        self.assertEqual(before, db.get_game_by_id(self.game['id']))
        self.assertFalse(draft['used'])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_stale_review_is_revalidated_before_apply(self):
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (999999, self.game['id']))
        draft = await recovery.preview(self.guild, self.actor, self.game['id'])
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=NULL WHERE id=?', (self.game['id'],))
        with self.assertRaisesRegex(ValueError, 'changed'):
            await recovery.apply(self.guild, self.actor, draft, 'new')
        self.guild.create_text_channel.assert_not_awaited()

    async def test_selector_review_click_opens_recovery_then_cancel_returns(self):
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (999999, self.game['id']))
        report = await readiness.inventory(self.guild, self.actor)
        self.assertEqual(report['states'][self.game['id']]['code'], readiness.REVIEW)
        view = ui.AdminGameSelection(self.guild, self.actor.id, report['games'],
                                     states=report['states'], checked_at=report['checked_at'])
        request = self.request()
        await view.toggle(request, self.game['id'])
        panel = request.edit_original_response.call_args.kwargs['view']
        self.assertIsInstance(panel, ui.RecoveryReview)
        self.assertIn('Repair', panel.content())
        self.assertIn('Create new channel', [b.label for b in panel.children])

        back = self.request()
        await panel.back(back)
        self.assertEqual(view.phase, 'select')
        self.assertIn('Recovery cancelled', back.edit_original_response.call_args.kwargs['content'])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_selector_existing_candidate_requires_second_confirmation(self):
        old = self.guild.add_channel('legacy-chat', None)
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?', (old.id, self.game['id']))
        # Force Review without modifying recovery evidence: an incomplete old
        # operation is represented by a stale missing canonical plus this legacy.
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=?,chat_channel_id=? WHERE id=?',
                         (999999, old.id, self.game['id']))
        report = await readiness.inventory(self.guild, self.actor)
        view = ui.AdminGameSelection(self.guild, self.actor.id, report['games'],
                                     states=report['states'], checked_at=report['checked_at'])
        request = self.request()
        await view.toggle(request, self.game['id'])
        panel = request.edit_original_response.call_args.kwargs['view']
        button = next(b for b in panel.children if b.label.startswith('Use #'))
        choose = self.request()
        await button.callback(choose)
        self.assertIn('Confirm Move Existing', [b.label for b in panel.children])
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], 999999)
        self.guild.create_text_channel.assert_not_awaited()
