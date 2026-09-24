import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

import discord
import test_role_settings as fixtures
from cogs.roles import RoleSelectionSession, RoleCategorySelect, ChooseRolesHubView, RoleToggleView
from services import role_service as roles, role_panel_service as panels, managed_message_service as managed
from database import db
from services.server_service import ServerMessageError


class ProfileWizardTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.RoleSettingsTests.setUp
    asyncSetUp = fixtures.RoleSettingsTests.asyncSetUp
    interaction = fixtures.RoleSettingsTests.interaction

    async def select(self, session, values):
        control = next(c for c in session.children if isinstance(c, RoleCategorySelect))
        control._values = values
        await control.callback(self.interaction())

    async def review(self, session):
        while session.step < 2:
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
            '# 👤 Profile Settings', '# 🎮 Gaming Setup',
            '# 🔔 Interests & Notifications', '# 💡 Missing something?'])
        self.assertEqual(len(self.board.messages), 4)
        self.assertNotIn('Suggest Role', [b.label for b in messages[0].view.children])
        self.assertEqual([b.label for b in messages[-1].view.children], ['Suggest Role'])
        self.assertEqual(await panels.diagnostics(self.guild, messages=True), [])

    async def test_preselection_back_cancel_and_review_before_save(self):
        for key in ('gender-female', 'age-25-34', 'pc', 'playstation', 'giveaways'):
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
        for expected in ('Non-binary / Diverse', '25–34'):
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
        unrelated += [roles.preference_role(self.guild, 'base', key) for key in ('pc', 'giveaways', 'gaming-deals')]
        self.member.roles = unrelated + [original]
        session = RoleSelectionSession(self.member)
        await self.select(session, ['gender-diverse'])
        session.selected_keys.add('age-25-34')
        await self.review(session)
        await asyncio.gather(session.confirm_selection(self.interaction()), session.confirm_selection(self.interaction()))
        self.assertTrue(all(r in self.member.roles for r in unrelated))
        self.assertNotIn(original, self.member.roles)
        for key in ('gender-diverse', 'age-25-34'):
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
        clear = next(b for b in RoleToggleView('Gender').children if b.label == 'Prefer not to say')
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
        await roles.toggle_preference(self.member, 'base', 'gender-female')
        await session.confirm_selection(self.interaction())
        self.assertIn(roles.preference_role(self.guild, 'base', 'gender-female'), self.member.roles)
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
        db.upsert_managed_role(role_id=self.game['role_id'], role_kind='base', role_key='gender-male')
        with self.assertRaises(ValueError):
            RoleSelectionSession(self.member)
        with self.assertRaises(ValueError):
            await roles.toggle_preference(self.member, 'base', 'gender-male')
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
        self.assertIn('Profile Review', session.status_text())
        self.assertFalse(any(isinstance(c, RoleCategorySelect) for c in session.children))
        self.member.add_roles.assert_not_called()

    async def legacy_panel(self):
        old_key = f'role_message:{self.guild.id}:notifications'
        message = await self.board.send(content='# 👤 About You\nLegacy personal controls')
        message.pinned = True
        template = managed.load(panels.message_keys(self.guild, self.board)['intro'])
        template.update(key=old_key, message_id=message.id, content=message.content,
                        content_hash=managed.digest(message.content), customized=False)
        managed.store(template)
        db.set_setting(old_key, message.id)
        return old_key, message

    async def test_explicit_repair_retires_only_owned_panel_and_language_registry(self):
        old_key, old = await self.legacy_panel()
        language = await self.guild.create_role(name='English')
        db.upsert_managed_role(role_id=language.id, role_kind='base', role_key='english')
        self.member.roles.append(language)
        unrelated = await self.board.send(content='Member message')
        unrelated.author.id = self.member.id
        ids = {key: db.get_setting(value) for key, value in panels.message_keys(self.guild, self.board).items()}
        await panels.refresh(self.guild)
        self.assertIn(old.id, self.board.messages)
        self.assertIsNotNone(db.get_managed_role_by_key('base', 'english'))
        await panels.sync(self.guild, repair=True)
        await panels.sync(self.guild, repair=True)
        self.assertNotIn(old.id, self.board.messages)
        self.assertEqual(db.get_setting(old_key), '')
        self.assertTrue(managed.load(old_key)['retired'])
        self.assertEqual(db.get_managed_role_by_key('legacy-profile', 'english')['role_id'], language.id)
        self.assertIsNone(db.get_managed_role_by_key('base', 'english'))
        self.assertIn(language, self.member.roles)
        self.assertIn(unrelated.id, self.board.messages)
        self.assertEqual(ids, {key: db.get_setting(value) for key, value in panels.message_keys(self.guild, self.board).items()})
        self.assertEqual(await panels.diagnostics(self.guild, messages=True), [])

    async def test_retirement_rejects_changed_fingerprint_and_retains_failed_delete(self):
        key, message = await self.legacy_panel()
        original = message.content
        message.content = 'Changed outside GamerHQ'
        with self.assertRaises(ServerMessageError):
            await panels.retire_obsolete(self.guild)
        self.assertEqual(db.get_setting(key), str(message.id))
        message.content = original
        with patch.object(message, 'delete', AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied'))):
            with self.assertRaises(discord.Forbidden):
                await panels.retire_obsolete(self.guild)
        self.assertEqual(db.get_setting(key), str(message.id))
        self.assertFalse(managed.load(key).get('retired', False))

    async def test_visible_copy_and_optional_playstyle(self):
        from services.onboarding_service import WELCOME_COPY
        self.assertIn('optional Gender and Age', WELCOME_COPY)
        self.assertIn('English-language server', WELCOME_COPY)
        self.assertNotIn('languages', WELCOME_COPY)
        copy = '\n'.join(m.content for m in self.board.messages.values())
        for absent in ('About You', 'Language', 'Gender', 'Age group', 'No active', 'configured', 'Playstyle'):
            self.assertNotIn(absent, copy)
        self.assertIn('## Choose your platforms', copy)
        self.assertIn('Select the platforms you usually play on.', copy)
        self.assertIn('Choose the events, streams and gaming content you want to hear about.', copy)
        controls = [b.custom_id for m in self.board.messages.values() for b in m.view.children]
        self.assertFalse(any('gender-' in cid or 'age-' in cid or cid.endswith(('english', 'german')) for cid in controls))
        with patch.dict(roles.ROLE_GROUPS, {roles.PLAYSTYLE_GROUP: (roles.RoleOption('test-style', 'Test Style', '🎯'),)}), \
                patch.dict(managed.ACTIONS, {'ROLE_test-style': ('Test Style', 'gamerhq:preference:base:test-style')}):
            await panels.sync(self.guild)
            setup_key = panels.message_keys(self.guild, self.board)['gaming_content']
            message = self.board.messages[int(db.get_setting(setup_key))]
            self.assertIn('## Choose your playstyle', message.content)
            self.assertIn('Test Style', [b.label for b in message.view.children])

    async def test_quick_platform_changes_do_not_conflict_with_personal_save(self):
        session = RoleSelectionSession(self.member)
        await self.select(session, ['gender-female'])
        await RoleToggleView('🎮 Gaming Setup').children[0].callback(self.interaction())
        await self.review(session)
        await session.confirm_selection(self.interaction())
        self.assertIn(roles.preference_role(self.guild, 'base', 'pc'), self.member.roles)
        self.assertIn(roles.preference_role(self.guild, 'base', 'gender-female'), self.member.roles)
        with self.assertRaises(ValueError):
            await roles.save_profile(self.member, {}, set(), {'pc'})

    async def test_old_language_controls_removed_from_custom_pin_on_repair(self):
        key = panels.message_keys(self.guild, self.board)['language']
        state = managed.load(key)
        state['customized'] = True
        state['buttons'].append(dict(label='English', emoji='🇬🇧', type='ACTION', target='ROLE_english', enabled=True))
        managed.store(state)
        mid, content = state['message_id'], state['content']
        await panels.sync(self.guild, repair=True)
        self.assertEqual(managed.load(key)['message_id'], mid)
        self.assertEqual(self.board.messages[mid].content, content)
        self.assertNotIn('ROLE_english', [b['target'] for b in managed.load(key)['buttons']])
