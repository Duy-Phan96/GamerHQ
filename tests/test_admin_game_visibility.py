"""Bulk admin drafts delegate to the existing visibility lifecycle, fully offline."""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import test_game_visibility as fixtures
from cogs import game_visibility_selector as ui
from database import db
from services import game_visibility_service as service
from services import game_channel_service as channels


class AdminVisibilityTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.VisibilityTests.setUp
    asyncSetUp = fixtures.VisibilityTests.asyncSetUp
    make_game = fixtures.VisibilityTests.make_game
    create = fixtures.VisibilityTests.create
    remove_shared_category = fixtures.VisibilityTests.remove_shared_category
    legacy = fixtures.VisibilityTests.legacy
    set_visible = fixtures.VisibilityTests.set_visible

    def request(self, user=None):
        return SimpleNamespace(guild=self.guild, user=user or self.actor,
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock(), is_done=lambda: False),
            edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))

    def selector(self, games=None):
        return ui.AdminGameSelection(self.guild, self.actor.id, games or db.get_all_games(active_only=True))

    async def test_open_admin_selector_is_private_and_preselects_visibility_not_member_roles(self):
        self.actor.roles = []
        request = self.request()
        await ui.open_selector(request)
        args = request.response.send_message.call_args.kwargs
        self.assertTrue(args['ephemeral'])
        self.assertFalse(args['allowed_mentions'].everyone)
        view = args['view']
        game_button = next(b for b in view.children if b.custom_id == f'gamerhq:admin_visibility:game:{self.game["id"]}')
        self.assertEqual(game_button.style, discord.ButtonStyle.success)
        self.guild.fetch_channels.assert_not_awaited()
        self.guild.create_text_channel.assert_not_awaited()

    async def test_toggle_drafts_and_cancel_leave_database_roles_and_channels_unchanged(self):
        original = db.get_game_by_id(self.game['id'])
        view = self.selector()
        await view.toggle(self.request(), self.game['id'])
        self.assertEqual(view.pending, {self.game['id']: False})
        self.assertEqual(db.get_game_by_id(self.game['id']), original)
        await view.cancel(self.request())
        self.assertEqual(view.phase, 'closed')
        self.guild.create_text_channel.assert_not_awaited()
        self.assertTrue(db.get_game_by_id(self.game['id'])['selectable'])

    async def test_active_hidden_games_included_and_inactive_excluded(self):
        second = await self.make_game('Ark')
        db.set_game_selectable(second['id'], False)
        with db.connect() as conn:
            conn.execute('UPDATE games SET active=0 WHERE id=?', (self.game['id'],))
        view = self.selector()
        self.assertIn(second['id'], view.games)
        self.assertNotIn(self.game['id'], view.games)
        self.assertFalse(view.is_visible(second['id']))

    async def test_120_games_pagination_keeps_choices_and_bounded_components(self):
        games = [dict(self.game, id=5000+n, name=f'{chr(65+n%26)} Game {n:03}', selectable=n%2) for n in range(120)]
        games.append(dict(self.game, id=9000, name='123 Other'))
        view = self.selector(games)
        first = next(g['id'] for g in view.page_games() if g['id'] != 9000)
        await view.toggle(self.request(), first)
        original = dict(view.pending)
        await view.navigate(self.request(), delta=1)
        await view.navigate(self.request(), browse=True)
        await view.navigate(self.request(), group='#')
        await view.toggle(self.request(), 9000)
        await view.navigate(self.request(), group=ui.POPULAR)
        self.assertEqual(view.pending[first], original[first])
        self.assertIn(9000, view.pending)
        for group in view.groups:
            view.group, view.browsing = group, False
            for page in range(view.pages()):
                view.page = page
                view.rebuild()
                self.assertLessEqual(len(view.children), 25)
                self.assertEqual(len({b.custom_id for b in view.children}), len(view.children))
                self.assertTrue(all(sum(b.row == row for b in view.children) <= 5 for row in range(5)))
        self.guild.create_text_channel.assert_not_awaited()

    async def test_show_page_stages_already_visible_games_for_missing_channel_setup(self):
        view = self.selector()
        await view.set_page(self.request(), True)
        self.assertEqual(view.pending, {self.game['id']: True})
        self.assertIn('1 show / set up', view.content())
        self.guild.create_text_channel.assert_not_awaited()
        await view.clear(self.request())
        self.assertEqual(view.pending, {})

    async def test_page_actions_never_hide_games_from_other_pages(self):
        games = [dict(self.game, id=5000+n, name=f'Alpha {n:03}') for n in range(36)]
        view = self.selector(games)
        page_ids = {g['id'] for g in view.page_games()}
        await view.set_page(self.request(), False)
        self.assertEqual(set(view.pending), page_ids)
        self.assertTrue(all(view.is_visible(g['id']) for g in games if g['id'] not in page_ids))
        await view.navigate(self.request(), delta=1)
        self.assertEqual(set(view.pending), page_ids)

    async def test_non_admin_wrong_actor_cross_guild_and_expired_sessions_are_denied(self):
        for mode in ('actor', 'guild', 'permission', 'expiry'):
            view, request = self.selector(), self.request()
            if mode == 'actor': request.user = SimpleNamespace(id=999)
            elif mode == 'guild': request.guild = SimpleNamespace(id=999)
            elif mode == 'expiry': view.expires = 0
            else: self.guild.get_member = lambda _: None
            await view.toggle(request, self.game['id'])
            self.assertEqual(view.pending, {})
            request.response.send_message.assert_awaited_once()
            self.guild.get_member = lambda uid: self.actor if uid == self.actor.id else None

    async def test_review_is_read_only_and_one_inventory_per_batch(self):
        second = await self.make_game('Ark')
        self.remove_shared_category()
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: True, second['id']: True})
        self.assertEqual(len(draft['plans']), 2)
        self.assertTrue(all(p['category_action'] == 'create' for p in draft['plans']))
        self.guild.fetch_channels.assert_awaited_once()
        self.guild.fetch_roles.assert_awaited_once()
        self.guild.create_category.assert_not_awaited()
        self.guild.create_text_channel.assert_not_awaited()

    async def test_three_shows_reuse_own_new_parent_without_stale_plan_failure(self):
        self.remove_shared_category()
        games = [self.game, await self.make_game('Ark'), await self.make_game('Valheim')]
        draft = await service.preview_batch(self.guild, self.actor, {g['id']: True for g in games})
        results = await service.apply_batch(self.guild, self.actor, draft)
        self.assertTrue(all(r['ok'] for r in results), results)
        self.guild.create_category.assert_awaited_once()
        self.assertEqual(self.guild.create_text_channel.await_count, 3)
        self.assertEqual(len(channels.category(self.guild).channels), 3)
        with self.assertRaises(ValueError):
            await service.apply_batch(self.guild, self.actor, draft)
        self.assertEqual(self.guild.create_text_channel.await_count, 3)

    async def test_mixed_hide_and_show_preserve_history_and_unselected_game(self):
        room = await self.set_visible(True)
        room.messages[88] = SimpleNamespace(content='keep me')
        second, untouched = await self.make_game('Ark'), await self.make_game('Valheim')
        db.set_game_selectable(second['id'], False)
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: False, second['id']: True})
        results = await service.apply_batch(self.guild, self.actor, draft)
        self.assertTrue(all(r['ok'] for r in results), results)
        self.assertFalse(room.overwrites_for(self.guild.get_role(self.game['role_id'])).view_channel)
        self.assertEqual(room.messages[88].content, 'keep me')
        self.assertTrue(db.get_game_by_id(untouched['id'])['selectable'])
        self.assertIsNone(db.get_game_by_id(untouched['id'])['channel_id'])
        show = await service.preview_batch(self.guild, self.actor, {self.game['id']: True})
        await service.apply_batch(self.guild, self.actor, show)
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], room.id)

    async def test_detached_legacy_migration_and_new_channel_in_one_batch(self):
        old, chat, other = await self.legacy(detached=True)
        second = await self.make_game('Ark')
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: True, second['id']: True})
        results = await service.apply_batch(self.guild, self.actor, draft)
        self.assertTrue(all(r['ok'] for r in results), results)
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], chat.id)
        self.assertEqual(chat.messages[4000].content, 'preserved history')
        self.assertIsNone(other.category_id)

    async def test_stale_game_selection_is_blocked_before_plan_or_mutation(self):
        old = {self.game['id']: dict(self.game)}
        db.set_game_selectable(self.game['id'], False)
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: True}, expected_games=old)
        self.assertEqual(draft['plans'], [])
        self.assertIn('changed since', draft['blocked'][0]['reason'])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_stale_channel_fails_only_that_game_other_ready_game_applies(self):
        room = await self.set_visible(True)
        second = await self.make_game('Ark')
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: False, second['id']: True})
        room.name = 'newer-edited-name'
        results = await service.apply_batch(self.guild, self.actor, draft)
        self.assertFalse(results[0]['ok'])
        self.assertTrue(results[1]['ok'], results)
        self.assertEqual(room.name, 'newer-edited-name')
        self.assertTrue(db.get_game_by_id(self.game['id'])['selectable'])

    async def test_external_parent_change_between_items_not_silently_adopted(self):
        second = await self.make_game('Ark')
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: True, second['id']: True})
        async def progress(done, total):
            if done == 1:
                channels.category(self.guild).name = 'external-new-name'
        result = await service.apply_batch(self.guild, self.actor, draft, progress=progress)
        self.assertTrue(result[0]['ok'])
        self.assertFalse(result[1]['ok'])
        self.assertEqual(channels.category(self.guild).name, 'external-new-name')
        self.assertEqual(self.guild.create_text_channel.await_count, 1)

    async def test_collective_soft_limit_requires_explicit_override(self):
        games = [self.game, await self.make_game('Ark'), await self.make_game('Valheim')]
        with patch('config.GAME_CHANNEL_SOFT_LIMIT', 1):
            draft = await service.preview_batch(self.guild, self.actor, {g['id']: True for g in games})
            self.assertTrue(draft['override'])
            self.assertEqual([p['override'] for p in draft['plans']], [False, True, True])
            with self.assertRaisesRegex(ValueError, 'soft limit'):
                await service.apply_batch(self.guild, self.actor, draft)
            self.guild.create_text_channel.assert_not_awaited()
            results = await service.apply_batch(self.guild, self.actor, draft, override=True)
        self.assertTrue(all(r['ok'] for r in results), results)

    async def test_projected_capacity_blocks_excess_without_silent_skips(self):
        second = await self.make_game('Ark')
        for n in range(49): self.guild.add_channel(f'existing-{n}', self.gaming)
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: True, second['id']: True})
        self.assertEqual(len(draft['plans']), 1)
        self.assertEqual(len(draft['blocked']), 1)
        self.assertIn('50-channel', draft['blocked'][0]['reason'])
        self.guild.create_text_channel.assert_not_awaited()

    async def test_blocked_permissions_are_named_and_not_bypassed(self):
        room = await self.set_visible(True)
        extra = await self.guild.create_role(name='Extra viewers')
        room.overwrites[extra] = discord.PermissionOverwrite(view_channel=True)
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: False})
        self.assertFalse(draft['plans'])
        self.assertIn('additional access', draft['blocked'][0]['reason'])
        self.assertTrue(room.overwrites[extra].view_channel)

    async def test_concurrent_confirmation_is_single_use(self):
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: True})
        results = await asyncio.gather(service.apply_batch(self.guild, self.actor, draft),
                                       service.apply_batch(self.guild, self.actor, draft), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.guild.create_text_channel.assert_awaited_once()

    async def test_back_preserves_drafts_and_invalidates_old_confirmation(self):
        view = self.selector()
        await view.set_page(self.request(), False)
        request = self.request()
        await view.review(request)
        panel = request.edit_original_response.call_args.kwargs['view']
        self.assertEqual(view.phase, 'review')
        await panel.back(self.request())
        self.assertEqual(view.pending, {self.game['id']: False})
        with patch.object(service, 'apply_batch', AsyncMock()) as apply:
            await panel.confirm(self.request())
        apply.assert_not_awaited()

    async def test_ui_one_confirmation_applies_many_and_result_is_paginated(self):
        games = [self.game, await self.make_game('Ark'), await self.make_game('Valheim')]
        view = self.selector(games)
        await view.set_page(self.request(), True)
        request = self.request()
        await view.review(request)
        panel = request.edit_original_response.call_args.kwargs['view']
        self.assertIn('ALL 3 ready games', panel.content())
        await panel.confirm(self.request())
        self.assertEqual(view.phase, 'result')
        self.assertIn('3 completed', panel.content())
        await panel.confirm(self.request())
        self.assertEqual(self.guild.create_text_channel.await_count, 3)

    async def test_long_names_and_reasons_fit_message_and_button_limits(self):
        games = [dict(self.game, id=1000+n, name='😀 @everyone **' * 30) for n in range(15)]
        view = self.selector(games)
        self.assertTrue(all(len(b.label.encode('utf-16-le')) <= 160 for b in view.children))
        draft = dict(plans=[], blocked=[dict(name=g['name'], reason='@everyone' * 300) for g in games], override=False)
        panel = ui.BatchReview(view, draft)
        self.assertLessEqual(len(panel.content().encode('utf-16-le')) // 2, 2000)
        self.assertNotIn('@everyone', panel.content())
        self.assertIn('1/3', panel.content())

    async def test_same_slug_in_batch_is_blocked_and_limit_rejects_oversized_request(self):
        other = await self.make_game('A!rk')
        first = await self.make_game('A?rk')
        draft = await service.preview_batch(self.guild, self.actor, {first['id']: True, other['id']: True})
        self.assertEqual(len(draft['plans']), 1)
        self.assertIn('same channel name', draft['blocked'][0]['reason'])
        with self.assertRaises(ValueError):
            await service.preview_batch(self.guild, self.actor, {n: True for n in range(51)})
        self.guild.create_text_channel.assert_not_awaited()

    async def test_interrupted_remote_create_not_retried_by_batch(self):
        error = discord.HTTPException(SimpleNamespace(status=502, reason='Bad Gateway'), 'uncertain')
        self.guild.create_text_channel.side_effect = error
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: True})
        result = await service.apply_batch(self.guild, self.actor, draft)
        self.assertFalse(result[0]['ok'])
        again = await service.preview_batch(self.guild, self.actor, {self.game['id']: True})
        self.assertFalse(again['plans'])
        self.assertEqual(len(again['blocked']), 1)
        self.guild.create_text_channel.assert_awaited_once()

    async def test_expired_and_revoked_batch_never_applies(self):
        draft = await service.preview_batch(self.guild, self.actor, {self.game['id']: True})
        draft['created'] = time.time() - 241
        with self.assertRaises(ValueError): await service.apply_batch(self.guild, self.actor, draft)
        draft['created'] = time.time()
        self.guild.get_member = lambda _: None
        with self.assertRaises(ValueError): await service.apply_batch(self.guild, self.actor, draft)
        self.guild.create_text_channel.assert_not_awaited()
