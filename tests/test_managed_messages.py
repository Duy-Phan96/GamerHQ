"""Offline editor acceptance tests over real persistence and stateful Discord fakes."""
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import test_onboarding as fixtures
from database import db
from services import managed_message_service as managed, support_service as support
from services.server_setup_service import repair_server
from services.server_service import ServerMessageError
from cogs import managed_messages as ui


def link(label='Public link', url='https://example.com/public'):
    return dict(label=label, emoji='🔗', type='LINK', target=url, enabled=True)


class ManagedMessageTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    async def asyncSetUp(self):
        managed._locks.clear()
        self.admin = SimpleNamespace(id=20, guild_permissions=discord.Permissions(administrator=True))
        self.owner = SimpleNamespace(id=90, guild_permissions=discord.Permissions())
        self.mod = SimpleNamespace(id=30, guild_permissions=discord.Permissions(manage_messages=True))
        self.member = SimpleNamespace(id=40, guild_permissions=discord.Permissions())
        self.guild.owner_id = self.owner.id
        self.members = {u.id: u for u in (self.admin, self.owner, self.mod, self.member)}
        self.guild.get_member = self.members.get
        await repair_server(self.guild, self.bot)
        self.key = support.message_key(self.guild, 'household')

    def interaction(self, user=None):
        return SimpleNamespace(guild=self.guild, user=user or self.admin,
                               response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock(),
                                                        send_modal=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
                               followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    def draft(self, key=None):
        return managed.load(key or self.key)

    def message(self, state):
        return self.guild.get_channel(state['channel_id']).messages[state['message_id']]

    async def test_owner_admin_only_and_revoked_permissions(self):
        for user in (self.admin, self.owner):
            self.assertTrue(managed.authorized(self.guild, user))
        for user in (self.mod, self.member):
            self.assertFalse(managed.authorized(self.guild, user))
            with self.assertRaises(ServerMessageError):
                await managed.save(self.guild, user, self.draft(), confirmed=True)
        view = ui.Main(ui.Session(self.guild, self.admin.id), self.draft())
        self.members[self.admin.id] = self.member
        self.assertFalse(await view.interaction_check(self.interaction()))
        with self.assertRaises(ServerMessageError):
            await managed.save(self.guild, self.admin, self.draft(), confirmed=True)

    async def test_channel_single_autoselect_and_multiple_selection(self):
        states = await managed.available(self.guild)
        self.assertEqual(len(states), 15)
        view = ui.Channels(ui.Session(self.guild, self.admin.id), states)
        interaction = self.interaction()
        amazon = self.draft(support.message_key(self.guild, 'amazon'))
        await view.choose(interaction, str(amazon['channel_id']))
        self.assertIsInstance(interaction.response.edit_message.call_args.kwargs['view'], ui.Main)
        from services.role_panel_service import channel as role_channel
        await view.choose(interaction, str(role_channel(self.guild).id))
        self.assertIsInstance(interaction.response.edit_message.call_args.kwargs['view'], ui.Messages)
        self.assertEqual(len(interaction.response.edit_message.call_args.kwargs['view'].children[0].options), 4)
        # Exercise the generic multi-message menu with two supported boards mapped to one channel.
        other = copy.deepcopy(amazon)
        other['channel_id'] = self.draft()['channel_id']
        view = ui.Channels(ui.Session(self.guild, self.admin.id), [self.draft(), other])
        await view.choose(interaction, str(self.draft()['channel_id']))
        menu = interaction.response.edit_message.call_args.kwargs['view']
        self.assertIsInstance(menu, ui.Messages)
        self.assertEqual({o.label for o in menu.children[0].options}, {'Electricity', 'Amazon'})

    async def test_unknown_manual_pins_and_wrong_author_excluded(self):
        state = self.draft()
        channel = self.guild.get_channel(state['channel_id'])
        manual = self.add_message(channel, 'Manual admin pin', author=123, pinned=True)
        unknown_bot = self.add_message(channel, 'Unmapped bot pin', pinned=True)
        available = await managed.available(self.guild)
        self.assertNotIn(manual.id, [s['message_id'] for s in available])
        self.assertNotIn(unknown_bot.id, [s['message_id'] for s in available])
        db.set_setting(self.key, manual.id)
        self.assertNotIn(self.key, [s['key'] for s in await managed.available(self.guild)])
        with self.assertRaises(ServerMessageError):
            await managed.save(self.guild, self.admin, state, confirmed=True)
        self.assertEqual(manual.content, 'Manual admin pin')

    async def test_markdown_saved_in_place_independent_partner_and_audit(self):
        draft = self.draft()
        message = self.message(draft)
        other = self.draft(support.message_key(self.guild, 'amazon'))
        channel = message.channel
        count = channel.sends
        draft['content'] = '# Neues Angebot\n\n**Markdown** und `code`\n- Item\n<#123> @everyone'
        draft['buttons'] = [link(), *draft['buttons']]
        result = await managed.save(self.guild, self.admin, draft, confirmed=True)
        self.assertEqual(message.content, draft['content'])
        self.assertEqual(message.id, result['message_id'])
        self.assertTrue(message.pinned)
        self.assertEqual(channel.sends, count)
        self.assertEqual(managed.load(other['key']), other)
        self.assertEqual([b.label for b in message.view.children], [b['label'] for b in draft['buttons']])
        with db.connect() as conn:
            audit = dict(conn.execute('SELECT * FROM managed_message_audit').fetchone())
        self.assertEqual(audit['actor_id'], self.admin.id)
        self.assertEqual(audit['setting_key'], self.key)
        self.assertEqual((audit['content_changed'], audit['buttons_changed']), (1, 1))
        self.assertNotIn('example.com', str(audit))
        self.assertEqual(len(audit['after_hash']), 64)

    async def test_oversized_and_empty_content_no_partial_save(self):
        before = self.draft()
        for body in ('x'*2001, '😀'*1001, '   '):
            draft = copy.deepcopy(before)
            draft['content'] = body
            with self.assertRaises(ServerMessageError):
                await managed.save(self.guild, self.admin, draft, confirmed=True)
            self.assertEqual(self.draft(), before)
            self.assertEqual(self.message(before).content, before['content'])

    async def test_preview_and_cancel_do_not_write(self):
        before = self.draft()
        draft = copy.deepcopy(before)
        draft['content'] = '# Draft only'
        view = ui.Main(ui.Session(self.guild, self.admin.id), draft)
        interaction = self.interaction()
        await view.preview(interaction)
        args = interaction.response.send_message.call_args
        self.assertEqual(args.args[0], draft['content'])
        self.assertTrue(all(b.disabled and b.url is None for b in args.kwargs['view'].children))
        confirm = interaction.followup.send.call_args.kwargs['view']
        await confirm.cancel(interaction)
        self.assertTrue(view.session.finished)
        self.assertEqual(self.draft(), before)
        self.assertEqual(self.message(before).content, before['content'])

    async def test_saved_preview_snapshot_cannot_be_mutated(self):
        draft = self.draft()
        draft['content'] = 'Previewed'
        confirm = ui.Confirm(ui.Session(self.guild, self.admin.id), draft)
        draft['content'] = 'Not previewed'
        await confirm.save(self.interaction())
        self.assertEqual(self.draft()['content'], 'Previewed')
        self.assertTrue(confirm.session.finished)

    async def test_custom_content_buttons_survive_restart_repair_and_health(self):
        draft = self.draft()
        draft['content'] = '# Completely different heading\n\nCustom offer'
        draft['buttons'] = [draft['buttons'][0], link('New public link')]
        draft['buttons'][0]['label'] = 'Contact us'
        draft['buttons'][0]['enabled'] = False
        await managed.save(self.guild, self.admin, draft, confirmed=True)
        managed._locks.clear()
        db.init_db()
        before = self.draft()
        await repair_server(self.guild, self.bot)
        await support.sync_support_messages(self.guild)
        after = self.draft()
        self.assertEqual(after['content'], before['content'])
        self.assertEqual(after['buttons'], before['buttons'])
        self.assertEqual(after['message_id'], before['message_id'])
        self.assertEqual(await managed.health(self.guild), [])
        from services.health_service import scan
        findings = await scan(self.guild)
        self.assertTrue(all(f.state == 'PASS' for f in findings if f.name in {'electricity pin', 'Managed message registry'}))

    async def test_reset_confirmation_restores_latest_defaults(self):
        draft = self.draft()
        draft['content'], draft['buttons'] = 'Custom', [link()]
        await managed.save(self.guild, self.admin, draft, confirmed=True)
        with self.assertRaises(ServerMessageError):
            await managed.save(self.guild, self.admin, self.draft(), reset=True)
        before = self.draft()
        ui_view = ui.Main(ui.Session(self.guild, self.admin.id), before)
        interaction = self.interaction()
        await ui_view.reset(interaction)
        self.assertEqual(self.draft(), before)
        confirmation = interaction.followup.send.call_args.kwargs['view']
        self.assertTrue(confirmation.reset)
        await confirmation.save(self.interaction())
        result = self.draft()
        self.assertFalse(result['customized'])
        self.assertEqual(result['content'], result['default_content'])
        self.assertEqual(result['buttons'], result['default_buttons'])

    async def test_stale_and_concurrent_edits_cannot_overwrite(self):
        drafts = [self.draft(), self.draft()]
        drafts[0]['content'], drafts[1]['content'] = 'First', 'Second'
        results = await asyncio.gather(*(managed.save(self.guild, self.admin, d, confirmed=True) for d in drafts), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ServerMessageError) for r in results), 1)
        self.assertEqual(self.draft()['content'], 'First')

    async def test_defaults_update_invalidates_open_draft(self):
        draft = self.draft()
        from services.server_service import upsert_fixed_message
        await upsert_fixed_message(self.message(draft).channel, setting_key=self.key, content='New default', view=support.section_view('household', None))
        with self.assertRaisesRegex(ServerMessageError, 'stale'):
            await managed.save(self.guild, self.admin, draft, confirmed=True)

    async def test_unsafe_links_and_arbitrary_actions_rejected(self):
        draft = self.draft()
        for url in ('javascript:alert(1)', 'file:///private', 'data:text/plain,test', 'http://example.com',
                    'https://' + 'name:password@' + 'example.com', 'https://example.com?api_key=hidden',
                    'https://example.com?access_token=hidden', 'https://', 'https://bad host.com', 'https://example.com:bad',
                    'https://.com', 'https://example.com/#access_token=hidden'):
            with self.subTest(url=url), self.assertRaises(ServerMessageError):
                managed.validate(self.guild, self.key, draft['content'], [link(url=url)])
        for target in ('eval', 'DELETE_SERVER', 'FINANCE_REQUEST'):
            with self.assertRaises(ServerMessageError):
                managed.validate(self.guild, self.key, draft['content'], [dict(label='Action', emoji='', type='ACTION', target=target, enabled=True)])
        with self.assertRaises(ServerMessageError):
            managed.validate(self.guild, self.key, draft['content'], [draft['buttons'][0]] * 2)
        with self.assertRaises(ServerMessageError):
            managed.validate(self.guild, self.key, draft['content'], [link()] * 26)

    async def test_link_add_edit_remove_reorder_and_enabled(self):
        draft = self.draft()
        before = self.draft()
        view = ui.Buttons(ui.Session(self.guild, self.admin.id), draft)
        modal = ui.ButtonModal(view, link())
        modal.label_input._value = 'Added'
        modal.emoji_input._value = '🔗'
        modal.url_input._value = 'https://example.com/added'
        interaction = self.interaction()
        await modal.on_submit(interaction)
        view = interaction.response.edit_message.call_args.kwargs['view']
        self.assertEqual(draft['buttons'][-1]['label'], 'Added')
        view.selected = 1
        modal = ui.ButtonModal(view, draft['buttons'][1], 1)
        modal.label_input._value, modal.emoji_input._value = 'Edited', ''
        modal.url_input._value = 'https://example.com/edited'
        await modal.on_submit(interaction)
        view = interaction.response.edit_message.call_args.kwargs['view']
        await view.up(interaction)
        self.assertEqual(draft['buttons'][0]['label'], 'Edited')
        view = interaction.response.edit_message.call_args.kwargs['view']
        await view.toggle(interaction)
        self.assertFalse(draft['buttons'][0]['enabled'])
        view = interaction.response.edit_message.call_args.kwargs['view']
        await view.remove(interaction)
        self.assertEqual(draft['buttons'], before['buttons'])
        self.assertEqual(self.draft(), before)

    async def test_registered_callbacks_and_restart_custom_ids(self):
        from cogs.tickets import SupportOffers, TicketEntry
        from cogs.suggestions import SuggestionEntryView
        from cogs.roles import OnboardingEntry, ChooseRolesHubView, RoleToggleView
        from services.role_panel_service import SECTIONS
        views = [SupportOffers(), TicketEntry(), SuggestionEntryView(), OnboardingEntry(), ChooseRolesHubView()]
        views.extend(RoleToggleView(group) for _, group, _ in SECTIONS)
        registered = {b.custom_id for view in views for b in view.children if b.custom_id}
        for state in await managed.available(self.guild):
            rendered = managed.render(state['buttons'])
            self.assertTrue(all(b.custom_id in registered for b in rendered.children if not b.url))
        action = self.draft()['buttons'][0]
        view = managed.render([action])
        with patch.object(SupportOffers, 'request', AsyncMock()) as request:
            await view.children[0].callback(self.interaction())
            self.assertEqual(request.call_args.args[1], 'ELECTRICITY_REQUEST')

    async def test_http_failure_durable_pending_then_repair(self):
        draft = self.draft()
        message = self.message(draft)
        draft['content'] = 'Durably saved draft'
        failure = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'test')
        with patch.object(message, 'edit', AsyncMock(side_effect=failure)):
            with self.assertRaisesRegex(ServerMessageError, 'stored'):
                await managed.save(self.guild, self.admin, draft, confirmed=True)
        self.assertTrue(self.draft()['pending'])
        self.assertTrue(await managed.health(self.guild))
        await support.sync_support_messages(self.guild)
        self.assertFalse(self.draft()['pending'])
        self.assertEqual(message.content, draft['content'])
        self.assertEqual(message.id, draft['message_id'])

    async def test_fingerprint_change_requires_review_without_duplicate(self):
        state = self.draft()
        message = self.message(state)
        message.content = 'Changed outside editor'
        sends = message.channel.sends
        with self.assertRaises(ServerMessageError):
            await managed.save(self.guild, self.admin, state, confirmed=True)
        with self.assertRaises(ServerMessageError):
            await support.sync_support_messages(self.guild)
        self.assertEqual(message.channel.sends, sends)
        self.assertEqual(message.content, 'Changed outside editor')

    async def test_core_boards_customization_and_suggestion_handler(self):
        keys = [key for key, spec in managed.specs(self.guild).items() if spec[0] in {'Welcome', 'Guide', 'Suggestions', 'Need Support'}]
        for key in keys:
            state = self.draft(key)
            state['content'] = '# Custom ' + state['label']
            await managed.save(self.guild, self.admin, state, confirmed=True)
        await repair_server(self.guild, self.bot)
        for key in keys:
            state = self.draft(key)
            self.assertEqual(self.message(state).content, '# Custom ' + state['label'])
        state = self.draft(f'suggestions_entry:{self.guild.id}')
        interaction = self.interaction()
        await self.message(state).view.children[0].callback(interaction)
        interaction.response.send_modal.assert_awaited_once()

    async def test_health_detects_bad_registry_and_remains_read_only(self):
        state = self.draft()
        state['buttons'][0]['target'] = 'ARBITRARY_CALLBACK'
        managed.store(state)
        before = managed.records(self.guild)
        self.assertTrue(await managed.health(self.guild))
        self.assertEqual(managed.records(self.guild), before)
        self.assertNotIn(self.key, [s['key'] for s in await managed.available(self.guild)])

    async def test_content_modal_permission_and_preserved_newlines(self):
        draft = self.draft()
        parent = ui.Main(ui.Session(self.guild, self.admin.id), draft)
        modal = ui.ContentModal(parent)
        modal.body._value = '# Heading\n\n**bold**\n- item'
        await modal.on_submit(self.interaction(self.member))
        self.assertNotEqual(draft['content'], str(modal.body))
        await modal.on_submit(self.interaction())
        self.assertEqual(draft['content'], str(modal.body))
        self.assertNotEqual(self.draft()['content'], draft['content'])

    async def test_action_menu_only_offers_board_allowlist(self):
        draft = self.draft()
        draft['buttons'] = []
        view = ui.Actions(ui.Session(self.guild, self.admin.id), draft)
        self.assertEqual([o.value for o in view.children[0].options], ['ELECTRICITY_REQUEST'])
        await view.choose(self.interaction(), 'ELECTRICITY_REQUEST')
        self.assertEqual(draft['buttons'][0]['target'], 'ELECTRICITY_REQUEST')

    async def test_support_disclosure_exact(self):
        self.assertEqual(support.DISCLOSURE, 'Some links may be affiliate or referral links.')

    async def test_health_missing_message_channel_and_duplicate_mapping(self):
        state = self.draft()
        channel = self.message(state).channel
        original = channel.messages.pop(state['message_id'])
        self.assertTrue(await managed.health(self.guild))
        channel.messages[state['message_id']] = original
        self.guild.text_channels.remove(channel)
        self.assertTrue(await managed.health(self.guild, messages=False))
        self.guild.text_channels.append(channel)
        other = self.draft(support.message_key(self.guild, 'amazon'))
        other['channel_id'] = state['channel_id']
        other['message_id'] = state['message_id']
        other['content_hash'] = state['content_hash']
        db.set_setting(other['key'], state['message_id'])
        managed.store(other)
        self.assertTrue(await managed.health(self.guild))
        self.assertNotIn(self.key, [s['key'] for s in await managed.available(self.guild)])
        with self.assertRaisesRegex(ServerMessageError, 'Multiple'):
            await managed.save(self.guild, self.admin, state, confirmed=True)

    async def test_deleted_custom_message_repair_preserves_customization(self):
        state = self.draft()
        state['content'] = 'Custom surviving a deleted message'
        await managed.save(self.guild, self.admin, state, confirmed=True)
        await self.message(state).delete()
        await support.sync_support_messages(self.guild)
        current = self.draft()
        self.assertEqual(current['content'], state['content'])
        self.assertNotEqual(current['message_id'], state['message_id'])
        self.assertTrue(self.message(current).pinned)
        other = self.draft(support.message_key(self.guild, 'amazon'))
        self.assertGreater(current['message_id'], other['message_id'])
        # Customized messages are not replaced just to restore chronological order.
        await support.sync_support_messages(self.guild)
        self.assertEqual(self.draft()['message_id'], current['message_id'])

    async def test_editor_pending_delivery_closes_without_false_cancel(self):
        state = self.draft()
        state['content'] = 'Pending body'
        view = ui.Confirm(ui.Session(self.guild, self.admin.id), state)
        failure = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'test')
        interaction = self.interaction()
        with patch.object(self.message(state), 'edit', AsyncMock(side_effect=failure)):
            await view.save(interaction)
        self.assertTrue(view.session.finished)
        args = interaction.response.edit_message.call_args.kwargs
        self.assertIn('stored', args['content'])
        self.assertIsNone(args['view'])

    async def test_maximum_buttons_render_without_extra_control_rows(self):
        state = self.draft()
        buttons = [link(str(i)) for i in range(25)]
        managed.validate(self.guild, state['key'], state['content'], buttons)
        for preview in (False, True):
            view = managed.render(buttons, preview=preview)
            self.assertEqual(len(view.to_components()), 5)
            self.assertTrue(all(len(row['components']) == 5 for row in view.to_components()))

    async def test_pending_partner_migration_blocks_editor(self):
        db.set_setting(f'partner_reorder:{self.guild.id}', 'pending')
        with self.assertRaisesRegex(ServerMessageError, 'migration'):
            await managed.save(self.guild, self.admin, self.draft(), confirmed=True)

    async def test_definite_discord_rejection_restores_editable_state(self):
        state = self.draft()
        before = self.draft()
        state['content'] = 'Rejected edit'
        failure = discord.HTTPException(SimpleNamespace(status=400, reason='Bad Request'), 'Invalid emoji')
        with patch.object(self.message(state), 'edit', AsyncMock(side_effect=failure)):
            with self.assertRaisesRegex(ServerMessageError, 'rejected'):
                await managed.save(self.guild, self.admin, state, confirmed=True)
        current = self.draft()
        self.assertFalse(current['pending'])
        self.assertEqual(current['content'], before['content'])
        self.assertEqual(current['buttons'], before['buttons'])
        self.assertEqual(await managed.health(self.guild), [])
