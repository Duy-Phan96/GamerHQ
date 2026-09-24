"""Offline onboarding, opt-in roles, independent boards and migration contracts."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import discord
import test_onboarding as fixtures
from database import db
from services import role_service as roles, role_panel_service as panels
from services import managed_message_service as managed
from services.server_setup_service import repair_server
from services.health_service import scan
from cogs.roles import RoleToggleView, RoleSelectionSession, OnboardingEntry
from cogs.games import GameSelectionSession, ChooseGamesButtons


class RoleSettingsTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    async def asyncSetUp(self):
        from config import DISPLAY_GROUP_ORDER
        self.game = db.upsert_custom_game('Example Game', '🎮', DISPLAY_GROUP_ORDER[0])
        db.set_game_selectable(self.game['id'], True)
        role = await self.guild.create_role(name='🎮 Example Game')
        db.set_game_role(self.game['id'], role.id)
        self.game = db.get_game_by_id(self.game['id'])
        self.member = MagicMock(spec=discord.Member)
        self.member.id, self.member.guild, self.member.roles = 77, self.guild, []
        async def add(*rs, **kwargs): self.member.roles.extend(r for r in rs if r not in self.member.roles)
        async def remove(*rs, **kwargs): self.member.roles[:] = [r for r in self.member.roles if r not in rs]
        self.member.add_roles = AsyncMock(side_effect=add)
        self.member.remove_roles = AsyncMock(side_effect=remove)
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        self.guild.get_member = lambda mid: self.member if mid == self.member.id else None
        roles._preference_locks.clear(); roles._role_sync_locks.clear(); panels._locks.clear()
        await panels.sync(self.guild)
        self.board = panels.channel(self.guild)

    def interaction(self):
        return SimpleNamespace(guild=self.guild, user=self.member,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    async def test_five_independent_pinned_panels_and_no_legacy_roles(self):
        keys = panels.message_keys(self.guild, self.board)
        self.assertEqual(len(keys), 5)
        self.assertEqual(len(self.board.messages), 5)
        for section, key in keys.items():
            msg = await self.board.fetch_message(int(db.get_setting(key)))
            self.assertTrue(msg.pinned)
            self.assertIsNotNone(managed.load(key))
            self.assertTrue(msg.view.is_persistent())
            self.assertNotIn('Competitive', msg.content)
            self.assertNotIn('Casual', msg.content)
            self.assertNotIn('LFG Pings', msg.content)
        self.assertTrue(self.board.overwrites_for(self.guild.default_role).view_channel)
        self.assertFalse(self.board.overwrites_for(self.guild.default_role).send_messages)

    async def test_sync_reuses_roles_messages_and_language_id(self):
        ids = [r.id for r in self.guild.roles]
        mids = list(self.board.messages)
        language = db.get_managed_role_by_key('base', 'english')['role_id']
        await panels.sync(self.guild); await panels.sync(self.guild)
        self.assertEqual(ids, [r.id for r in self.guild.roles])
        self.assertEqual(mids, list(self.board.messages))
        self.assertEqual(language, db.get_managed_role_by_key('base', 'english')['role_id'])

    async def test_each_content_button_adds_then_removes_only_its_role(self):
        for button in RoleToggleView('📰 Gaming Content').children:
            interaction = self.interaction()
            await button.callback(interaction)
            key = button.custom_id.split(':')[-1]
            role = roles.preference_role(self.guild, 'base', key)
            self.assertIn(role, self.member.roles)
            await button.callback(interaction)
            self.assertNotIn(role, self.member.roles)
            self.assertTrue(interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_broken_privileged_and_unknown_mappings_fail_without_assignment(self):
        role = roles.preference_role(self.guild, 'base', 'gaming-news')
        role.permissions = discord.Permissions(administrator=True)
        with self.assertRaises(ValueError): await roles.toggle_preference(self.member, 'base', 'gaming-news')
        with self.assertRaises(ValueError): await roles.toggle_preference(self.member, 'base', 'competitive')
        db.delete_managed_role(roles.preference_role(self.guild, 'base', 'gaming-deals').id)
        await RoleToggleView('📰 Gaming Content').children[1].callback(self.interaction())
        self.member.add_roles.assert_not_called()

    async def test_nonmember_button_denied(self):
        interaction = self.interaction(); interaction.guild = None
        await RoleToggleView('📰 Gaming Content').children[0].callback(interaction)
        self.member.add_roles.assert_not_called()

    async def test_individual_panel_recovery_leaves_others_untouched(self):
        key = panels.message_keys(self.guild, self.board)['gaming_content']
        msg = self.board.messages.pop(int(db.get_setting(key)))
        edits = {m.id: m.edits for m in self.board.messages.values()}
        await panels.refresh(self.guild, section='gaming_content')
        self.assertNotEqual(str(msg.id), db.get_setting(key))
        self.assertEqual(edits, {mid: self.board.messages[mid].edits for mid in edits})
        self.assertEqual(len(self.board.messages), 5)

    async def test_owner_repair_restores_missing_role_panel(self):
        key = panels.message_keys(self.guild, self.board)['language']
        self.board.messages.pop(int(db.get_setting(key)))
        _, failed = await repair_server(self.guild, self.bot)
        self.assertEqual(failed, [])
        self.assertIn(int(db.get_setting(key)), self.board.messages)

    async def test_deprecated_managed_roles_retired_without_touching_members(self):
        old = await self.guild.create_role(name='Competitive')
        db.upsert_managed_role(role_id=old.id, role_kind='base', role_key='competitive')
        self.member.roles.append(old)
        await panels.sync(self.guild)
        self.assertIsNone(db.get_managed_role_by_key('base', 'competitive'))
        self.assertIn(old, self.guild.roles); self.assertIn(old, self.member.roles)
        event = db.create_lfg_event(guild_id=self.guild.id, game_id=self.game['id'], host_id=77,
            title='Still works', start_at=2000000000, max_players=5, invite_lead_minutes=10)
        self.assertIsNotNone(db.get_lfg_event(event['id']))

    async def test_lfg_selection_is_independent_and_can_be_disabled(self):
        game_role = self.guild.get_role(self.game['role_id'])
        lfg_role = roles.preference_role(self.guild, 'lfg', str(self.game['id']))
        session = GameSelectionSession(self.member, [self.game])
        session.pending_ids.add(self.game['id'])
        await session.confirm_selection(self.interaction())
        self.assertIn(game_role, self.member.roles); self.assertNotIn(lfg_role, self.member.roles)
        self.assertTrue(await roles.toggle_preference(self.member, 'lfg', str(self.game['id'])))
        self.assertIn(lfg_role, self.member.roles)
        self.assertFalse(await roles.toggle_preference(self.member, 'lfg', str(self.game['id'])))
        self.assertIn(game_role, self.member.roles)

    async def test_lfg_session_and_public_ping_target_correct_game_only(self):
        session = GameSelectionSession(self.member, [self.game], notifications=True)
        session.pending_ids.add(self.game['id'])
        await session.confirm_selection(self.interaction())
        role = roles.preference_role(self.guild, 'lfg', str(self.game['id']))
        self.assertEqual(self.member.roles, [role])
        prefix, mentions = roles.lfg_notification(self.guild, self.game['id'])
        self.assertEqual(prefix, f'<@&{role.id}>\n'); self.assertEqual(mentions.roles, [role])
        self.assertEqual(roles.lfg_notification(self.guild, self.game['id'], private=True)[0], '')
        self.assertEqual(roles.lfg_notification(self.guild, self.game['id'], already_posted=True)[0], '')
        self.assertTrue(ChooseGamesButtons().is_persistent())

    async def test_profile_order_has_no_game_selection(self):
        session = RoleSelectionSession(self.member)
        for expected in ('Gender', 'Age', 'Language', 'Platform', 'Playstyle', 'Interests & Notifications'):
            self.assertIn(expected, session.status_text())
            await session.next_step(self.interaction())
        self.assertIn('Profile Review', session.status_text())
        self.assertFalse(any(isinstance(c, GameSelectionSession) for c in session.children))
        self.assertEqual(self.member.roles, [])
        self.assertTrue(OnboardingEntry().is_persistent())

    async def test_profile_choices_exclusive_and_other_member_cannot_advance(self):
        await roles.toggle_preference(self.member, 'base', 'gender-male', exclusive='Gender')
        await roles.toggle_preference(self.member, 'base', 'gender-female', exclusive='Gender')
        self.assertEqual(len(self.member.roles), 1)
        self.assertEqual(self.member.roles[0], roles.preference_role(self.guild, 'base', 'gender-female'))
        interaction = self.interaction()
        session = RoleSelectionSession(self.member)
        session.member_id = 999
        await session.next_step(interaction)
        interaction.response.send_message.assert_awaited_once()

    async def test_health_detects_missing_duplicate_mappings_without_writes(self):
        self.assertEqual(await panels.diagnostics(self.guild, messages=True), [])
        keys = panels.message_keys(self.guild, self.board)
        db.set_setting(keys['language'], db.get_setting(keys['notifications']))
        role = roles.preference_role(self.guild, 'base', 'gaming-news')
        self.guild.roles.remove(role)
        with db.connect() as conn: before = list(conn.iterdump())
        issues = await panels.diagnostics(self.guild, messages=True)
        self.assertTrue(any('Gaming News' in i for i in issues))
        self.assertTrue(any('Duplicate' in i for i in issues))
        await scan(self.guild, self.bot, messages=False)
        with db.connect() as conn: self.assertEqual(before, list(conn.iterdump()))

    async def test_custom_panel_content_survives_refresh(self):
        key = panels.message_keys(self.guild, self.board)['language']
        state = managed.load(key); state['customized'] = True
        # Simulate a previously confirmed editor save with matching delivered fingerprint.
        state['content'] = '# 🌐 Language\nCustom explanation'; state['content_hash'] = managed.digest(state['content'])
        self.board.messages[state['message_id']].content = state['content']; managed.store(state)
        await panels.refresh(self.guild, section='language')
        self.assertEqual(self.board.messages[state['message_id']].content, state['content'])

    async def test_deleted_notification_role_is_repaired_once(self):
        role = roles.preference_role(self.guild, 'base', 'gaming-deals')
        self.guild.roles.remove(role)
        await panels.sync(self.guild)
        replacement = roles.preference_role(self.guild, 'base', 'gaming-deals')
        self.assertNotEqual(role.id, replacement.id)
        await panels.sync(self.guild)
        self.assertEqual(replacement.id, roles.preference_role(self.guild, 'base', 'gaming-deals').id)

    async def test_lfg_mapping_cannot_grant_a_game_access_role(self):
        db.upsert_managed_role(role_id=self.game['role_id'], role_kind='lfg', role_key=str(self.game['id']))
        with self.assertRaises(ValueError):
            await roles.toggle_preference(self.member, 'lfg', str(self.game['id']))
        self.member.add_roles.assert_not_called()

    async def test_many_game_preferences_are_paginated(self):
        from cogs.games import CategoryGameSelect
        games = [dict(self.game, id=n, name=f'Game {n}') for n in range(100, 126)]
        session = GameSelectionSession(self.member, games, notifications=True)
        first = next(c for c in session.children if isinstance(c, CategoryGameSelect))
        self.assertEqual(len(first.options), 25)
        await next(c for c in session.children if getattr(c, 'label', '') == 'Next').callback(self.interaction())
        second = next(c for c in session.children if isinstance(c, CategoryGameSelect))
        self.assertEqual([o.value for o in second.options], ['125'])

    async def test_concurrent_sync_and_restart_views_preserve_identity(self):
        import asyncio
        from cogs.roles import Roles
        ids = [r.id for r in self.guild.roles]
        messages = list(self.board.messages)
        await asyncio.gather(panels.sync(self.guild), panels.sync(self.guild))
        self.assertEqual(ids, [r.id for r in self.guild.roles])
        self.assertEqual(messages, list(self.board.messages))
        bot = SimpleNamespace(add_view=MagicMock())
        await Roles(bot).cog_load()
        self.assertEqual(bot.add_view.call_count, 6)
        self.assertTrue(all(call.args[0].is_persistent() for call in bot.add_view.call_args_list))

    async def test_game_rename_reuses_notification_role_and_other_game_is_independent(self):
        role = roles.preference_role(self.guild, 'lfg', str(self.game['id']))
        with db.connect() as conn:
            conn.execute('UPDATE games SET name=? WHERE id=?', ('Renamed Game', self.game['id']))
        other = db.upsert_custom_game('Second Game', '🎮', self.game['display_group'])
        db.set_game_selectable(other['id'], True)
        await roles.ensure_lfg_roles(self.guild)
        role.edit.assert_awaited_once_with(name='🔔 Renamed Game LFG', reason='GamerHQ game-library notification role name')
        self.assertEqual(role.id, roles.preference_role(self.guild, 'lfg', str(self.game['id'])).id)
        self.assertNotEqual(role.id, roles.preference_role(self.guild, 'lfg', str(other['id'])).id)

    async def test_health_reports_unreadable_history_without_mutating(self):
        async def forbidden(**kwargs):
            raise discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'history denied')
            yield
        self.board.history = forbidden
        issues = await panels.diagnostics(self.guild, messages=True)
        self.assertTrue(any('inspection incomplete' in item for item in issues))
