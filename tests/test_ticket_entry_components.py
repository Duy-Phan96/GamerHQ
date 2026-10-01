"""Discord round-trip component IDs must not make genuine ticket boards unusable."""
import asyncio
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.components import _component_factory
import test_onboarding as fixtures
from database import db
from services import managed_message_service as managed, ticket_entry_service as entries
from services import ticket_service as tickets
from services.server_service import ServerMessageError
from cogs import tickets as controls


class EntryComponentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.OnboardingTests.setUp(self)
        managed._locks.clear()
        for locks in (tickets._user_locks, tickets._ticket_locks, tickets._entry_locks, tickets._recovery_locks):
            locks.clear()
        self.guild.owner_id = 99
        self.owner = self.member(99)
        self.user = self.member(42)
        self.other = self.member(43)
        self.guild.get_member = lambda uid: {99: self.owner, 42: self.user, 43: self.other}.get(uid)
        self.guild._state.http.get_guild = AsyncMock(return_value={'owner_id': '99'})
        patcher = patch.object(discord, 'TextChannel', fixtures.FakeChannel)
        patcher.start(); self.addCleanup(patcher.stop)
        self.boards = {}
        for kind in entries.KINDS:
            spec = entries.definition(self.guild, kind)
            channel = self.guild.add_channel(spec['name'], self.start)
            message = fixtures.FakeMessage(channel, 200000000000001501 + len(self.boards), spec['content'], pinned=True)
            message.view = spec['view']
            message.attachments = []
            self.roundtrip(message)
            channel.messages[message.id] = message
            db.set_setting(spec['channel_key'], channel.id)
            db.set_setting(spec['key'], message.id)
            buttons = managed.serialize_view(spec['view'])
            managed.store(dict(key=spec['key'], guild_id=self.guild.id, channel_id=channel.id, message_id=message.id,
                label=spec['label'], content=message.content, content_hash=managed.digest(message.content),
                buttons=buttons, default_content=message.content, default_buttons=copy.deepcopy(buttons),
                customized=False, version=1, pending=False))
            self.boards[kind] = (spec, channel, message)

    def member(self, uid):
        user = MagicMock(spec=discord.Member)
        user.id, user.guild, user.bot, user.roles = uid, self.guild, False, [self.guild.default_role]
        return user

    def roundtrip(self, message, *, payload=None, offset=0):
        # Unlike the previous fixtures, parse actual received Discord components.
        received = copy.deepcopy(payload if payload is not None else message.view.to_components())
        number = offset
        for row in received:
            number += 1; row['id'] = number
            for button in row['components']:
                number += 1; button['id'] = number
        message.components = [_component_factory(row) for row in received]
        return received

    def snapshot(self):
        with db.connect() as conn:
            return '\n'.join(conn.iterdump())

    def erase_bindings(self, kind):
        spec, _, _ = self.boards[kind]
        with db.connect() as conn:
            conn.execute('DELETE FROM settings WHERE key IN (?,?)', (spec['key'], spec['channel_key']))
            conn.execute('DELETE FROM managed_message_content WHERE setting_key=?', (spec['key'],))

    def request(self, kind='support'):
        _, channel, message = self.boards[kind]
        return SimpleNamespace(guild=self.guild, guild_id=self.guild.id, channel_id=channel.id,
            message=message, user=self.user, response=SimpleNamespace(send_message=AsyncMock(),
            send_modal=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    async def test_real_received_ids_differ_but_payloads_match_both_known_defaults(self):
        for kind, (spec, channel, message) in self.boards.items():
            with self.subTest(kind=kind):
                before = [c.to_dict() for c in message.components]
                self.assertNotEqual(before, managed.render(managed.serialize_view(spec['view'])).to_components())
                self.assertTrue(entries._payload_matches(self.guild, spec, None, channel, message))
                self.assertEqual(before, [c.to_dict() for c in message.components])

    async def test_missing_all_bindings_then_confirm_repair_preserves_originals(self):
        for kind, (spec, channel, message) in self.boards.items():
            with self.subTest(kind=kind):
                self.erase_bindings(kind)
                before = self.snapshot()
                draft = await entries.preview(self.guild, self.owner, kind, hint=(channel.id, message.id))
                self.assertTrue(draft['repairable'], draft['reason'])
                self.assertEqual(before, self.snapshot())
                with self.assertRaises(ServerMessageError):
                    await entries.repair(self.guild, self.owner, draft)
                await entries.repair(self.guild, self.owner, draft, confirmed=True)
                self.assertTrue((await entries.diagnose(self.guild, kind))['ready'])
                self.assertIsNone(entries.binding_problem(self.request(kind), kind))
                self.assertEqual(str(channel.id), db.get_setting(spec['channel_key']))
                self.assertEqual(str(message.id), db.get_setting(spec['key']))
                self.assertEqual((channel.sends, message.edits), (0, 0))

    async def test_ready_board_and_redirect_survive_returned_component_ids(self):
        for kind, (_, channel, message) in self.boards.items():
            with self.subTest(kind=kind):
                result = await entries.diagnose(self.guild, kind)
                self.assertTrue(result['ready'], result['reason'])
                self.assertEqual(await entries.current_link(self.guild, self.user, kind),
                    f'https://discord.com/channels/{self.guild.id}/{channel.id}/{message.id}')

    async def test_old_payload_without_ids_is_still_supported(self):
        for spec, channel, message in self.boards.values():
            message.components = [_component_factory(c) for c in message.view.to_components()]
            self.assertTrue(entries._payload_matches(self.guild, spec, None, channel, message))

    async def test_customized_text_link_and_unicode_emoji_are_preserved(self):
        spec, channel, message = self.boards['support']
        state = managed.load(spec['key'])
        state['content'] = 'Our help desk\nTell us what went wrong.'
        state['buttons'][0].update(label='Ask the team', emoji='🎮')
        state['buttons'].append(dict(label='Help', emoji='', type='LINK', target='https://example.com/help', enabled=True))
        state.update(customized=True, content_hash=managed.digest(state['content']))
        managed.store(state)
        message.content, message.view = state['content'], managed.render(state['buttons'])
        self.roundtrip(message, offset=50)
        db.set_setting(spec['key'], '')
        draft = await entries.preview(self.guild, self.owner, 'support')
        self.assertTrue(draft['repairable'], draft['reason'])
        await entries.repair(self.guild, self.owner, draft, confirmed=True)
        saved = managed.load(spec['key'])
        self.assertEqual(saved['buttons'], state['buttons'])
        self.assertEqual(saved['content'], state['content'])
        self.assertTrue(saved['customized'])
        self.assertEqual(message.edits, 0)

    async def test_changed_controls_stay_rejected_with_exact_reason(self):
        spec, channel, message = self.boards['support']
        for field, value in [('custom_id', 'gamerhq:offers:electricity'), ('label', 'Changed'),
                             ('disabled', True), ('style', 4), ('url', 'https://example.com/changed'),
                             ('sku_id', '123456789012345678')]:
            with self.subTest(field=field):
                payload = message.view.to_components()
                payload[0]['components'][0][field] = value
                self.roundtrip(message, payload=payload)
                result = await entries.diagnose(self.guild, 'support')
                self.assertFalse(result['ready'] or result['repairable'])
                self.assertIn('controls', result['reason'])

    async def test_custom_emoji_identity_is_not_stripped(self):
        spec, channel, message = self.boards['support']
        self.guild.get_emoji = lambda _: SimpleNamespace(is_usable=lambda: True)
        state = managed.load(spec['key'])
        state['buttons'][0]['emoji'] = '<:help:123456789012345678>'
        managed.store(state)
        message.view = managed.render(state['buttons'])
        self.roundtrip(message)
        self.assertTrue(entries._payload_matches(self.guild, spec, state, channel, message))
        payload = message.view.to_components()
        payload[0]['components'][0]['emoji']['id'] = 123456789012345679
        self.roundtrip(message, payload=payload)
        self.assertFalse(entries._payload_matches(self.guild, spec, state, channel, message))

    async def test_duplicate_entries_are_not_adopted(self):
        _, channel, message = self.boards['support']
        self.erase_bindings('support')
        other = fixtures.FakeMessage(channel, message.id + 100, message.content, pinned=True)
        other.view = message.view; other.attachments = []
        self.roundtrip(other, offset=20)
        channel.messages[other.id] = other
        row = await entries.preview(self.guild, self.owner, 'support', hint=(channel.id, message.id))
        self.assertFalse(row['repairable'])
        self.assertIn('2 possible', row['reason'])

    async def test_changed_content_has_a_specific_reason(self):
        _, channel, message = self.boards['support']
        self.erase_bindings('support')
        message.content += '\nA different message body.'
        before = self.snapshot()
        row = await entries.preview(self.guild, self.owner, 'support', hint=(channel.id, message.id))
        self.assertFalse(row['repairable'])
        self.assertIn('text', row['reason'])
        self.assertEqual(before, self.snapshot())

    async def test_extra_content_wrong_source_and_nonbutton_components_stay_rejected(self):
        spec, channel, original = self.boards['support']
        for field, value in [('author', SimpleNamespace(id=321)), ('webhook_id', 123),
                             ('guild', SimpleNamespace(id=2)), ('attachments', [SimpleNamespace(id=10)]),
                             ('embeds', [discord.Embed(description='Unexpected')])]:
            with self.subTest(field=field):
                message = copy.copy(original); setattr(message, field, value)
                self.assertFalse(entries._payload_matches(self.guild, spec, None, channel, message))
        payload = [{'type': 1, 'components': [{'type': 3, 'custom_id': 'gamerhq:tickets:create',
                    'options': [{'label': 'Choose', 'value': 'x'}]}]}]
        message = copy.copy(original); self.roundtrip(message, payload=payload)
        self.assertFalse(entries._payload_matches(self.guild, spec, None, channel, message))

    async def test_real_control_edit_invalidates_confirmation(self):
        spec, channel, message = self.boards['support']
        self.erase_bindings('support')
        draft = await entries.preview(self.guild, self.owner, 'support', hint=(channel.id, message.id))
        self.assertTrue(draft['repairable'], draft['reason'])
        payload = message.view.to_components(); payload[0]['components'][0]['disabled'] = True
        self.roundtrip(message, payload=payload)
        before = self.snapshot()
        with self.assertRaisesRegex(ServerMessageError, 'changed'):
            await entries.repair(self.guild, self.owner, draft, confirmed=True)
        self.assertEqual(before, self.snapshot())

    async def test_repair_restart_click_form_submit_creates_one_private_ticket(self):
        _, channel, message = self.boards['support']
        self.erase_bindings('support')
        draft = await entries.preview(self.guild, self.owner, 'support', hint=(channel.id, message.id))
        self.assertTrue(draft['repairable'], draft['reason'])
        await entries.repair(self.guild, self.owner, draft, confirmed=True)
        category = self.guild.add_category(tickets.CATEGORY_NAME)
        category.overwrites = tickets.private(self.guild)
        db.set_setting(f'managed_category:{self.guild.id}:support-tickets', category.id)
        registered = []
        await controls.Tickets(SimpleNamespace(add_view=registered.append)).cog_load()
        request = self.request()
        entry = next(v for v in registered if isinstance(v, controls.TicketEntry))
        await entry.create.callback(request)
        request.response.send_modal.assert_awaited_once()
        modal = request.response.send_modal.call_args.args[0]
        modal.subject._value = 'Synthetic issue'
        modal.description._value = 'Test only: my game room is unavailable.'
        modal.feature._value = 'Voice'
        await modal.on_submit(request)
        saved = tickets.list_tickets(self.guild.id)
        self.assertEqual(len(saved), 1)
        ticket = self.guild.get_channel(saved[0]['channel_id'])
        self.assertEqual(ticket.category_id, category.id)
        tickets.check_private(ticket, self.user.id)
        self.assertIsNot(ticket.overwrites_for(self.other).view_channel, True)
        await modal.on_submit(request)
        self.assertEqual(len(tickets.list_tickets(self.guild.id)), 1)
        self.assertEqual(len(category.text_channels), 1)
        self.assertEqual((channel.sends, message.edits), (0, 0))
