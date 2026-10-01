"""Readiness is observed state, never selectable alone or an optimistic draft."""
import asyncio
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
from services import game_readiness_service as readiness
from services import game_visibility_service as visibility
from services import game_channel_service as channels


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.VisibilityTests.setUp
    asyncSetUp = fixtures.VisibilityTests.asyncSetUp
    make_game = fixtures.VisibilityTests.make_game
    create = fixtures.VisibilityTests.create
    remove_shared_category = fixtures.VisibilityTests.remove_shared_category
    legacy = fixtures.VisibilityTests.legacy
    set_visible = fixtures.VisibilityTests.set_visible

    def request(self, user=None):
        return SimpleNamespace(guild=self.guild, user=user or self.actor,
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(),
                defer=AsyncMock(), is_done=lambda: False),
            edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))

    def snapshot(self):
        with db.connect() as conn:
            return '\n'.join(conn.iterdump())

    async def selection(self):
        report = await readiness.inventory(self.guild, self.actor)
        return ui.AdminGameSelection(self.guild, self.actor.id, report['games'],
            states=report['states'], checked_at=report['checked_at'], error=report['error'])

    def game_button(self, view, gid=None):
        return next(b for b in view.children
                    if b.custom_id == f'gamerhq:admin_visibility:game:{gid or self.game["id"]}')

    async def status(self):
        return (await readiness.inventory(self.guild, self.actor))['states'][self.game['id']]

    async def test_visible_without_channel_warns_and_first_click_stages_setup(self):
        before = self.snapshot()
        view = await self.selection()
        self.assertEqual(view.game_state(self.game['id'])['code'], readiness.SETUP_NEEDED)
        self.assertNotEqual(self.game_button(view).style, discord.ButtonStyle.success)
        await view.toggle(self.request(), self.game['id'])
        self.assertEqual(view.pending, {self.game['id']: True})
        self.assertEqual(self.game_button(view).style, discord.ButtonStyle.primary)
        self.assertIn('Show pending', self.game_button(view).label)
        self.assertEqual(before, self.snapshot())
        self.guild.create_text_channel.assert_not_awaited()
        await view.toggle(self.request(), self.game['id'])
        self.assertFalse(view.pending)
        self.assertEqual(view.game_state(self.game['id'])['code'], readiness.SETUP_NEEDED)

    async def test_verified_ready_is_green_and_pending_hide_is_not_green(self):
        room = await self.set_visible(True)
        view = await self.selection()
        self.assertEqual(view.game_state(self.game['id'])['code'], readiness.READY)
        self.assertEqual(self.game_button(view).style, discord.ButtonStyle.success)
        before = self.snapshot()
        await view.toggle(self.request(), self.game['id'])
        self.assertFalse(view.pending[self.game['id']])
        self.assertNotEqual(self.game_button(view).style, discord.ButtonStyle.success)
        self.assertIn('Hide pending', self.game_button(view).label)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(room.overwrites_for(self.guild.get_role(self.game['role_id'])).view_channel)

    async def test_hidden_no_channel_and_retained_hidden_history_are_different(self):
        await self.set_visible(False)
        first = await self.status()
        self.assertEqual(first['code'], readiness.NOT_SET_UP)
        self.assertIsNone(first['channel_id'])
        room = await self.set_visible(True)
        room.messages[4001] = SimpleNamespace(content='Retain this synthetic history')
        await self.set_visible(False)
        second = await self.status()
        self.assertEqual(second['code'], readiness.HIDDEN)
        self.assertEqual(second['channel_id'], room.id)
        view = await self.selection()
        self.assertIn('History kept', self.game_button(view).label)
        await view.toggle(self.request(), self.game['id'])
        self.assertTrue(view.pending[self.game['id']])
        self.assertEqual(room.messages[4001].content, 'Retain this synthetic history')

    async def test_wrong_category_name_access_and_hide_policy_are_not_ready(self):
        room = await self.set_visible(True)
        for fault in ('category', 'name', 'access', 'hidden'):
            with self.subTest(fault=fault):
                category, name = room.category, room.name
                overwrites = {t: discord.PermissionOverwrite.from_pair(*o.pair()) for t, o in room.overwrites.items()}
                if fault == 'category': room.category = self.guild.add_category('Other area')
                elif fault == 'name': room.name = 'different-name'
                elif fault == 'access': room.overwrites[self.guild.get_role(self.game['role_id'])].send_messages = False
                else: db.set_setting(channels.hidden_key(self.guild, self.game['id']), '1')
                row = await self.status()
                self.assertEqual(row['code'], readiness.SETUP_NEEDED, row)
                self.assertTrue(row['target'])
                room.category, room.name, room.overwrites = category, name, overwrites
                db.set_setting(channels.hidden_key(self.guild, self.game['id']), '0')

    async def test_missing_linked_role_and_extra_access_require_review(self):
        room = await self.set_visible(True)
        role = self.guild.get_role(self.game['role_id'])
        self.guild.roles.remove(role)
        row = await self.status()
        self.assertEqual(row['code'], readiness.REVIEW)
        self.assertIn('role', row['reason'])
        self.guild.roles.append(role)
        extra = await self.guild.create_role(name='Additional viewers')
        room.overwrites[extra] = discord.PermissionOverwrite(view_channel=True)
        view = await self.selection()
        self.assertEqual(view.game_state(self.game['id'])['code'], readiness.REVIEW)
        before = self.snapshot()
        request = self.request()
        await view.toggle(request, self.game['id'])
        self.assertFalse(view.pending)
        self.assertIn('additional access', request.response.send_message.call_args.args[0])
        self.assertEqual(before, self.snapshot())

    async def test_missing_saved_channel_is_review_not_no_channel(self):
        await self.set_visible(True)
        self.guild.fetch_channels.side_effect = lambda: [c for c in self.guild.channels
            if c.id != db.get_game_by_id(self.game['id'])['channel_id']]
        row = await self.status()
        self.assertEqual(row['code'], readiness.REVIEW)
        self.assertIn('saved game chat', row['reason'])

    async def test_pending_malformed_and_inconsistent_operations_never_green(self):
        room = await self.set_visible(True)
        key = visibility.operation_key(self.guild, self.game['id'])
        for raw, expected in [
            (json.dumps({'state': 'PENDING', 'visible': True}), readiness.PENDING),
            ('invalid-json', readiness.REVIEW),
            ('[]', readiness.REVIEW),
            (json.dumps({'state': 'DONE', 'visible': 'true', 'channel_id': room.id}), readiness.REVIEW),
            (json.dumps({'state': 'DONE', 'visible': False, 'channel_id': room.id}), readiness.SETUP_NEEDED)]:
            with self.subTest(raw=raw):
                db.set_setting(key, raw)
                before = self.snapshot()
                self.assertEqual((await self.status())['code'], expected)
                self.assertEqual(before, self.snapshot())

    async def test_uncertain_creation_and_intentional_removal_are_not_ready(self):
        room = await self.set_visible(True)
        key = f'game_channel_creation:{self.guild.id}:{self.game["id"]}'
        db.set_setting(key, 'reserved')
        self.assertEqual((await self.status())['code'], readiness.REVIEW)
        db.set_setting(key, json.dumps({'state': 'CREATED', 'channel_id': room.id}))
        db.set_setting(f'game_channel_removed:{self.guild.id}:{self.game["id"]}', '1')
        row = await self.status()
        self.assertEqual(row['code'], readiness.REVIEW)
        self.assertIn('deliberately removed', row['reason'])

    async def test_unmapped_legacy_chat_in_games_is_not_canonical_ready(self):
        _, chat, _ = await self.legacy(detached=True)
        chat.category, chat.name = self.gaming, channels.slug(self.game['name'])
        chat.overwrites = channels.overwrites(self.guild, self.guild.get_role(self.game['role_id']), chat, visible=True)
        row = await self.status()
        self.assertEqual(row['code'], readiness.SETUP_NEEDED)
        self.assertIn('canonical', row['reason'])
        self.assertEqual(chat.messages[4000].content, 'preserved history')

    async def test_failed_inventory_is_unverified_even_if_cached_channel_exists(self):
        await self.set_visible(True)
        for error in (TimeoutError(), discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')):
            with self.subTest(error=type(error).__name__):
                self.guild.fetch_channels.side_effect = error
                before = self.snapshot()
                view = await self.selection()
                self.assertEqual(view.game_state(self.game['id'])['code'], readiness.UNVERIFIED)
                self.assertNotEqual(self.game_button(view).style, discord.ButtonStyle.success)
                await view.set_page(self.request(), True)
                self.assertFalse(view.pending)
                self.assertEqual(before, self.snapshot())

    async def test_rest_snapshot_wins_over_outdated_channel_cache(self):
        room = await self.set_visible(True)
        verified = copy.copy(room)
        verified.name = channels.slug(self.game['name'])
        room.name = 'stale-cache-name'
        self.guild.fetch_channels.side_effect = lambda: [verified if c.id == room.id else c for c in self.guild.channels]
        self.assertEqual((await self.status())['code'], readiness.READY)
        self.assertEqual(room.name, 'stale-cache-name')

    async def test_inventory_is_read_only_and_api_count_does_not_scale_with_games(self):
        for n in range(18): await self.make_game(f'Game {n:02}')
        self.guild.fetch_channels.reset_mock()
        self.guild.fetch_roles.reset_mock()
        before = self.snapshot()
        report = await readiness.inventory(self.guild, self.actor)
        self.assertEqual(len(report['games']), 19)
        self.guild.fetch_channels.assert_awaited_once()
        self.guild.fetch_roles.assert_awaited_once()
        self.assertEqual(before, self.snapshot())
        self.guild.create_text_channel.assert_not_awaited()
        self.guild.create_category.assert_not_awaited()
        view = ui.AdminGameSelection(self.guild, self.actor.id, report['games'], states=report['states'], checked_at=report['checked_at'])
        await view.navigate(self.request(), delta=1)
        await view.navigate(self.request(), browse=True)
        self.guild.fetch_channels.assert_awaited_once()

    async def test_revoked_access_during_inventory_does_not_display_game_data(self):
        async def roles():
            self.guild.get_member = lambda _: None
            return list(self.guild.roles)
        self.guild.fetch_roles.side_effect = roles
        request = self.request()
        await ui.open_selector(request)
        self.assertIsNone(request.edit_original_response.call_args.kwargs['view'])
        self.assertNotIn(self.game['name'], request.edit_original_response.call_args.kwargs['content'])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_expired_snapshot_is_not_green_and_refresh_keeps_drafts(self):
        await self.set_visible(True)
        view = await self.selection()
        await view.toggle(self.request(), self.game['id'])
        expected = copy.deepcopy(view.draft_games)
        view.checked_at = time.monotonic() - readiness.MAX_AGE - 1
        view.rebuild()
        self.assertNotEqual(self.game_button(view).style, discord.ButtonStyle.success)
        await view.refresh(self.request())
        self.assertEqual(view.pending, {self.game['id']: False})
        self.assertEqual(view.draft_games, expected)
        await view.clear(self.request())
        self.assertEqual(self.game_button(view).style, discord.ButtonStyle.success)

    async def test_refresh_does_not_rebase_a_changed_game_under_an_old_choice(self):
        view = await self.selection()
        await view.toggle(self.request(), self.game['id'])
        db.set_game_selectable(self.game['id'], False)
        await view.refresh(self.request())
        request = self.request()
        await view.review(request)
        panel = request.edit_original_response.call_args.kwargs['view']
        self.assertFalse(panel.draft['plans'])
        self.assertIn('changed since', panel.draft['blocked'][0]['reason'])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_apply_then_back_to_games_rechecks_actual_ready_and_hidden_states(self):
        view = await self.selection()
        await view.toggle(self.request(), self.game['id'])
        request = self.request()
        await view.review(request)
        panel = request.edit_original_response.call_args.kwargs['view']
        await panel.confirm(self.request())
        self.assertTrue(panel.results[0]['ok'])
        await panel.reopen(self.request())
        self.assertFalse(view.pending)
        self.assertEqual(self.game_button(view).style, discord.ButtonStyle.success)
        await view.toggle(self.request(), self.game['id'])
        request = self.request()
        await view.review(request)
        panel = request.edit_original_response.call_args.kwargs['view']
        await panel.confirm(self.request())
        await panel.reopen(self.request())
        self.assertEqual(view.game_state(self.game['id'])['code'], readiness.HIDDEN)
        self.assertNotEqual(self.game_button(view).style, discord.ButtonStyle.success)
        self.guild.create_text_channel.assert_awaited_once()

    async def test_partial_failure_refresh_never_paints_failed_game_green(self):
        await self.make_game('Ark')
        view = await self.selection()
        await view.set_page(self.request(), True)
        request = self.request()
        await view.review(request)
        panel = request.edit_original_response.call_args.kwargs['view']
        create = self.guild.create_text_channel
        original = create.side_effect
        calls = 0
        async def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1: raise discord.HTTPException(SimpleNamespace(status=502, reason='Bad Gateway'), 'uncertain')
            if original is not None: return await original(*args, **kwargs)
            return await create._mock_wraps(*args, **kwargs)
        create.side_effect = fail_once
        await panel.confirm(self.request())
        failed = next(r['game_id'] for r in panel.results if not r['ok'])
        await panel.reopen(self.request())
        self.assertNotEqual(view.game_state(failed)['code'], readiness.READY)
        self.assertNotEqual(self.game_button(view, failed).style, discord.ButtonStyle.success)
        self.assertFalse(view.pending)
