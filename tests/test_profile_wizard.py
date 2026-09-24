import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

import discord
import test_role_settings as fixtures
from cogs.roles import RoleSelectionSession, RoleCategorySelect, ChooseRolesHubView, RoleToggleView
from services import role_service as roles, role_panel_service as panels, managed_message_service as managed
from database import db


class ProfileWizardTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.RoleSettingsTests.setUp
    asyncSetUp = fixtures.RoleSettingsTests.asyncSetUp
    interaction = fixtures.RoleSettingsTests.interaction

    async def select(self, session, values):
        control = next(c for c in session.children if isinstance(c, RoleCategorySelect))
        control._values = values
        await control.callback(self.interaction())

    async def review(self, session):
        while session.step < 6:
            await session.next_step(self.interaction())

    async def test_grouped_structure_controls_and_legacy_ids_reused(self):
        keys = panels.message_keys(self.guild, self.board)
        before = {key: db.get_setting(value) for key, value in keys.items()}
        for key, value in keys.items():
            state = managed.load(value)
            state['content'] = panels.LEGACY_TITLES[key] + '\nOld default'
            state['content_hash'] = managed.digest(state['content'])
            self.board.messages[state['message_id']].content = state['content']
            managed.store(state)
        await panels.sync(self.guild)
        await panels.sync(self.guild)
        self.assertEqual(before, {key: db.get_setting(value) for key, value in keys.items()})
        messages = [self.board.messages[int(before[key])] for key in keys]
        self.assertEqual([m.content.split('\n')[0] for m in messages], [
            '# 👤 Profile Settings', '# 👤 About You', '# 🎮 Gaming Setup',
            '# 🔔 Interests & Notifications', '# 💡 Missing something?'])
        self.assertEqual(len(self.board.messages), 5)
        self.assertNotIn('Suggest Role', [b.label for b in messages[0].view.children])
        self.assertEqual([b.label for b in messages[-1].view.children], ['Suggest Role'])
        self.assertEqual(await panels.diagnostics(self.guild, messages=True), [])

    async def test_preselection_back_cancel_and_review_before_save(self):
        for key in ('gender-female', 'age-25-34', 'english', 'german', 'pc', 'playstation', 'giveaways'):
            self.member.roles.append(roles.preference_role(self.guild, 'base', key))
        session = RoleSelectionSession(self.member)
        control = session.children[0]
        self.assertEqual([o.value for o in control.options if o.default], ['gender-female'])
        await self.select(session, ['gender-diverse'])
        await session.next_step(self.interaction())
        await session.back(self.interaction())
        self.assertEqual(session.step, 0)
        self.assertIn('gender-diverse', session.selected_keys)
        await session.confirm_selection(self.interaction())
        self.member.add_roles.assert_not_called()
        await self.review(session)
        for expected in ('Non-binary / Diverse', '25–34', 'English, German', 'PC, PlayStation', 'Giveaways'):
            self.assertIn(expected, session.status_text())
        await session.cancel_selection(self.interaction())
        await session.confirm_selection(self.interaction())
        self.member.add_roles.assert_not_called()
        self.member.remove_roles.assert_not_called()

    async def test_save_differences_preserves_game_staff_integration_and_manual_roles(self):
        original = roles.preference_role(self.guild, 'base', 'gender-male')
        game = self.guild.get_role(self.game['role_id'])
        integration = await self.guild.create_role(name='Integration')
        integration.managed = True
        unrelated = [game, self.guild.mod, self.guild.custom, integration]
        self.member.roles = unrelated + [original]
        session = RoleSelectionSession(self.member)
        await self.select(session, ['gender-diverse'])
        session.selected_keys.update({'german', 'pc', 'gaming-deals'})
        await self.review(session)
        await asyncio.gather(session.confirm_selection(self.interaction()), session.confirm_selection(self.interaction()))
        self.assertTrue(all(r in self.member.roles for r in unrelated))
        self.assertNotIn(original, self.member.roles)
        for key in ('gender-diverse', 'german', 'pc', 'gaming-deals'):
            self.assertIn(roles.preference_role(self.guild, 'base', key), self.member.roles)
        self.member.add_roles.assert_awaited_once()
        self.member.remove_roles.assert_awaited_once()

    async def test_private_gender_clears_legacy_and_never_creates_hidden_choice_role(self):
        self.assertIsNone(db.get_managed_role_by_key('base', 'gender-unspecified'))
        legacy = await self.guild.create_role(name='Prefer not to say')
        db.upsert_managed_role(role_id=legacy.id, role_kind='base', role_key='gender-unspecified')
        self.member.roles = [legacy, roles.preference_role(self.guild, 'base', 'gender-male')]
        session = RoleSelectionSession(self.member)
        await self.select(session, ['__private__'])
        self.assertEqual(len(self.member.roles), 2)
        await self.review(session)
        await session.confirm_selection(self.interaction())
        self.assertEqual(self.member.roles, [])
        self.assertIn(legacy, self.guild.roles)
        await roles.toggle_preference(self.member, 'base', 'gender-female')
        clear = next(b for b in RoleToggleView('👤 About You').children if b.label == 'Prefer not to say')
        await clear.callback(self.interaction())
        self.assertEqual(self.member.roles, [])

    async def test_other_member_wrong_guild_timeout_and_missing_review_denied(self):
        session = RoleSelectionSession(self.member)
        interaction = self.interaction()
        interaction.user = SimpleNamespace(id=999)
        await session.next_step(interaction)
        self.assertEqual(session.step, 0)
        interaction = self.interaction()
        interaction.guild = SimpleNamespace(id=999)
        await session.next_step(interaction)
        self.assertEqual(session.step, 0)
        await session.on_timeout()
        await session.next_step(self.interaction())
        self.assertEqual(session.step, 0)
        self.member.add_roles.assert_not_called()

    async def test_concurrent_quick_edit_or_changed_mapping_rejects_stale_save(self):
        session = RoleSelectionSession(self.member)
        await self.review(session)
        await roles.toggle_preference(self.member, 'base', 'pc')
        await session.confirm_selection(self.interaction())
        self.assertIn(roles.preference_role(self.guild, 'base', 'pc'), self.member.roles)
        self.member.remove_roles.assert_not_called()
        session = RoleSelectionSession(self.member)
        session.selected_keys.add('gender-male')
        await self.review(session)
        old = roles.preference_role(self.guild, 'base', 'gender-male')
        self.guild.roles.remove(old)
        await roles.ensure_base_roles(self.guild)
        self.member.add_roles.reset_mock()
        await session.confirm_selection(self.interaction())
        self.member.add_roles.assert_not_called()

    async def test_unsafe_game_mapping_and_invalid_selection_fail_closed(self):
        db.upsert_managed_role(role_id=self.game['role_id'], role_kind='base', role_key='pc')
        with self.assertRaises(ValueError):
            RoleSelectionSession(self.member)
        with self.assertRaises(ValueError):
            await roles.toggle_preference(self.member, 'base', 'pc')
        self.member.add_roles.assert_not_called()

    async def test_persistent_entry_preloads_fresh_member_and_suggestion_is_separate(self):
        interaction = self.interaction()
        await ChooseRolesHubView().select_roles.callback(interaction)
        self.guild.fetch_member.assert_awaited_with(self.member.id)
        view = interaction.followup.send.call_args.kwargs['view']
        self.assertIsInstance(view, RoleSelectionSession)
        self.assertEqual(view.step, 0)
        interaction.response.send_modal = AsyncMock()
        await RoleToggleView('💡 Missing something?').children[0].callback(interaction)
        interaction.response.send_modal.assert_awaited_once()

    async def test_gender_age_choices_and_multi_select(self):
        self.assertEqual([o.label for o in roles.ROLE_GROUPS['Age group']], ['Under 18', '18–24', '25–34', '35+'])
        session = RoleSelectionSession(self.member)
        self.assertEqual([o.label for o in session.children[0].options], ['Male', 'Female', 'Non-binary / Diverse', 'Prefer not to say'])
        await session.next_step(self.interaction())
        self.assertEqual(session.children[0].max_values, 1)
        await session.next_step(self.interaction())
        await self.select(session, ['english', 'german'])
        await session.next_step(self.interaction())
        await self.select(session, ['pc', 'xbox'])
        self.assertTrue({'english', 'german', 'pc', 'xbox'} <= session.selected_keys)
        self.member.add_roles.assert_not_called()
