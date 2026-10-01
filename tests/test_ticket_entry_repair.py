"""Offline entry-binding repair; existing messages are never replaced or exposed."""
import asyncio
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import test_onboarding as fixtures
from database import db
from services import managed_message_service as managed, ticket_entry_service as entries
from services import ticket_service as tickets, support_service as support
from services.server_service import ServerMessageError, upsert_fixed_message
from cogs import tickets as controls, ticket_entry_repair as ui


class EntryRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.OnboardingTests.setUp(self)
        managed._locks.clear()
        self.guild.owner_id = 99
        self.owner = SimpleNamespace(id=99, guild=self.guild, bot=False)
        self.member = SimpleNamespace(id=42, guild=self.guild, bot=False)
        self.guild.get_member = lambda uid: {99: self.owner, 42: self.member}.get(uid)
        self.guild._state.http.get_guild = AsyncMock(return_value={'owner_id': '99'})
        async def fetch_channel(cid):
            result = self.guild.get_channel(cid)
            if result is None:
                raise fixtures.missing()
            return result
        self.guild.fetch_channel = AsyncMock(side_effect=fetch_channel)
        patcher = patch.object(discord, 'TextChannel', fixtures.FakeChannel)
        patcher.start(); self.addCleanup(patcher.stop)
        self.boards = {}
        for kind in entries.KINDS:
            spec = entries.definition(self.guild, kind)
            channel = self.guild.add_channel(spec['name'], self.start)
            message = fixtures.FakeMessage(channel, 200000000000000501 + len(self.boards), spec['content'], pinned=True)
            message.view = spec['view']
            self.components(message)
            channel.messages[message.id] = message
            db.set_setting(spec['channel_key'], channel.id)
            db.set_setting(spec['key'], message.id)
            buttons = managed.serialize_view(spec['view'])
            managed.store(dict(key=spec['key'], guild_id=self.guild.id, channel_id=channel.id, message_id=message.id,
                label=spec['label'], content=message.content, content_hash=managed.digest(message.content),
                buttons=buttons, default_content=message.content, default_buttons=copy.deepcopy(buttons),
                customized=False, version=1, pending=False))
            self.boards[kind] = (spec, channel, message)

    def components(self, message):
        message.components = [SimpleNamespace(to_dict=lambda row=row: copy.deepcopy(row)) for row in message.view.to_components()]
        message.attachments = []

    def request(self, kind='support', owner=True):
        spec, channel, message = self.boards[kind]
        return SimpleNamespace(guild=self.guild, guild_id=self.guild.id, channel_id=channel.id, message=message,
            user=self.owner if owner else self.member,
            response=SimpleNamespace(send_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock(),
                edit_message=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    def snapshot(self):
        with db.connect() as conn:
            return '\n'.join(conn.iterdump())

    def missing_mapping(self, kind='support', which='message'):
        spec, _, _ = self.boards[kind]
        db.set_setting(spec['key'] if which == 'message' else spec['channel_key'], '')

    async def test_healthy_support_opens_modal_without_ticket_or_write(self):
        request = self.request(owner=False)
        before = self.snapshot()
        await controls.TicketEntry().create.callback(request)
        request.response.send_modal.assert_awaited_once()
        self.assertEqual(before, self.snapshot())

    async def test_healthy_both_entries_are_read_only_and_ready(self):
        before = self.snapshot()
        for kind in entries.KINDS:
            row = await entries.diagnose(self.guild, kind)
            self.assertTrue(row['ready'], row['reason'])
            self.assertFalse(row['repairable'])
        self.assertEqual(before, self.snapshot())
        self.assertTrue(all(not m.edits and not c.sends for _, c, m in self.boards.values()))

    async def test_missing_message_and_channel_bindings_repaired_after_confirmation_only(self):
        for kind, which in [('support', 'message'), ('electricity', 'channel')]:
            self.missing_mapping(kind, which)
            spec, channel, message = self.boards[kind]
            before = self.snapshot()
            draft = await entries.preview(self.guild, self.owner, kind)
            self.assertTrue(draft['repairable'], draft['reason'])
            self.assertEqual(before, self.snapshot())
            with self.assertRaises(ServerMessageError):
                await entries.repair(self.guild, self.owner, draft)
            await entries.repair(self.guild, self.owner, draft, confirmed=True)
            self.assertIsNone(entries.binding_problem(self.request(kind), kind))
            self.assertEqual(str(message.id), db.get_setting(spec['key']))
            self.assertEqual(str(channel.id), db.get_setting(spec['channel_key']))
            self.assertEqual(channel.sends, 0)
            self.assertEqual(message.edits, 0)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM managed_message_audit WHERE action='entry-binding-repair'").fetchone()[0], 2)

    async def test_known_default_without_registry_is_registered_without_edit(self):
        spec, channel, message = self.boards['support']
        with db.connect() as conn:
            conn.execute('DELETE FROM managed_message_content WHERE setting_key=?', (spec['key'],))
        self.missing_mapping('support', 'channel')
        self.missing_mapping('support')
        draft = await entries.preview(self.guild, self.owner, 'support', hint=(channel.id, message.id))
        self.assertTrue(draft['repairable'], draft['reason'])
        await entries.repair(self.guild, self.owner, draft, confirmed=True)
        self.assertTrue((await entries.diagnose(self.guild, 'support'))['ready'])
        self.assertEqual(message.edits, 0)

    async def test_customized_content_and_buttons_preserved(self):
        spec, _, message = self.boards['support']
        state = managed.load(spec['key'])
        state['content'] = 'A customized help desk\nPlease explain the issue.'
        state['buttons'][0]['label'] = 'Ask our team'
        state.update(customized=True, content_hash=managed.digest(state['content']))
        managed.store(state)
        message.content, message.view = state['content'], managed.render(state['buttons'])
        self.components(message)
        self.missing_mapping()
        draft = await entries.preview(self.guild, self.owner, 'support')
        self.assertTrue(draft['repairable'], draft['reason'])
        await entries.repair(self.guild, self.owner, draft, confirmed=True)
        updated = managed.load(spec['key'])
        for field in ('content', 'buttons', 'customized', 'message_id'):
            self.assertEqual(updated[field], state[field])
        self.assertGreater(updated['version'], state['version'])
        self.assertEqual(message.edits, 0)

    async def test_disabled_action_and_pending_or_retired_states_are_not_reenabled(self):
        spec, _, message = self.boards['support']
        original = managed.load(spec['key'])
        for variant in ('disabled', 'pending', 'retired'):
            state = copy.deepcopy(original)
            if variant == 'disabled': state['buttons'][0]['enabled'] = False
            else: state[variant] = True
            managed.store(state)
            before = self.snapshot()
            self.assertIsNotNone(entries.binding_problem(self.request(), 'support'))
            row = await entries.preview(self.guild, self.owner, 'support')
            self.assertFalse(row['ready'] or row['repairable'])
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(message.edits, 0)

    async def test_wrong_author_wrong_channel_wrong_guild_and_lookalikes_rejected(self):
        for fault in ('author', 'channel', 'guild', 'content'):
            request = self.request()
            request.message = copy.copy(request.message)
            if fault == 'author': request.message.author = SimpleNamespace(id=1234)
            elif fault == 'channel': request.channel_id += 100
            elif fault == 'guild': request.guild = SimpleNamespace(id=2, me=self.guild.me)
            else: request.message.content = 'Changed content'
            self.assertIsNotNone(entries.binding_problem(request, 'support'))

    async def test_duplicate_or_modified_controls_require_review(self):
        spec, channel, message = self.boards['support']
        self.missing_mapping()
        other = fixtures.FakeMessage(channel, message.id + 100, message.content, pinned=True)
        other.view = message.view; self.components(other)
        channel.messages[other.id] = other
        row = await entries.preview(self.guild, self.owner, 'support')
        self.assertFalse(row['repairable'])
        self.assertIn('2 possible', row['reason'])
        channel.messages.pop(other.id)
        message.components = []
        row = await entries.preview(self.guild, self.owner, 'support')
        self.assertFalse(row['repairable'])
        self.assertIn('controls', row['reason'])

    async def test_disagreeing_existing_channels_are_not_silently_rebound(self):
        spec, _, _ = self.boards['support']
        db.set_setting(spec['channel_key'], self.boards['electricity'][1].id)
        row = await entries.preview(self.guild, self.owner, 'support')
        self.assertFalse(row['repairable'])
        self.assertIn('disagrees', row['reason'])

    async def test_permission_failure_not_reported_as_absence(self):
        self.guild.fetch_channel.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        row = await entries.preview(self.guild, self.owner, 'support')
        self.assertFalse(row['repairable'])
        self.assertIn('denied', row['reason'])
        self.assertNotIn('missing', row['reason'])

    async def test_scan_bound_is_respected(self):
        _, channel, _ = self.boards['support']
        for n in range(102):
            channel.messages[n] = fixtures.FakeMessage(channel, n, 'Unrelated content', author=42)
        self.missing_mapping()
        row = await entries.preview(self.guild, self.owner, 'support')
        self.assertFalse(row['repairable'])
        self.assertIn('scan limit', row['reason'])

    async def test_stale_content_mapping_and_duplicate_confirmation_blocked(self):
        self.missing_mapping()
        draft = await entries.preview(self.guild, self.owner, 'support')
        self.boards['support'][2].content += ' changed'
        with self.assertRaisesRegex(ServerMessageError, 'changed'):
            await entries.repair(self.guild, self.owner, draft, confirmed=True)
        self.boards['support'][2].content = self.boards['support'][0]['content']
        draft = await entries.preview(self.guild, self.owner, 'support')
        results = await asyncio.gather(*(entries.repair(self.guild, self.owner, draft, confirmed=True) for _ in range(2)), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ServerMessageError) for r in results), 1)
        self.assertTrue((await entries.diagnose(self.guild, 'support'))['ready'])

    async def test_owner_only_and_owner_transfer(self):
        self.missing_mapping()
        with self.assertRaises(ServerMessageError):
            await entries.preview(self.guild, self.member, 'support')
        draft = await entries.preview(self.guild, self.owner, 'support')
        self.guild._state.http.get_guild.return_value = {'owner_id': '88'}
        with self.assertRaisesRegex(ServerMessageError, 'ownership changed'):
            await entries.repair(self.guild, self.owner, draft, confirmed=True)
        self.assertEqual(db.get_setting(self.boards['support'][0]['key']), '')

    async def test_obsolete_entry_gets_verified_accessible_link(self):
        request = self.request(owner=False)
        request.message = copy.copy(request.message); request.message.id += 100
        await controls.TicketEntry().create.callback(request)
        view = request.followup.send.call_args.kwargs['view']
        self.assertIn(str(self.boards['support'][2].id), view.children[0].url)
        request.response.send_modal.assert_not_awaited()
        self.boards['support'][1].permissions_for = lambda _: discord.Permissions.none()
        self.assertIsNone(await entries.current_link(self.guild, self.member, 'support'))

    async def test_owner_reject_offers_check_not_automatic_repair(self):
        self.missing_mapping()
        before = self.snapshot()
        request = self.request()
        await controls.TicketEntry().create.callback(request)
        view = request.followup.send.call_args.kwargs['view']
        self.assertIsInstance(view, ui.EntryChecks)
        self.assertEqual(before, self.snapshot())
        await view.children[0].callback(request)
        review = request.edit_original_response.call_args.kwargs['view']
        self.assertIsInstance(review, ui.EntryReview)
        self.assertEqual(before, self.snapshot())
        await review.confirm(request)
        self.assertIn('repaired', request.edit_original_response.call_args.kwargs['content'])
        self.assertTrue((await entries.diagnose(self.guild, 'support'))['ready'])

    async def test_cancel_and_nonowner_cannot_confirm(self):
        self.missing_mapping()
        draft = await entries.preview(self.guild, self.owner, 'support')
        view = ui.EntryReview(draft)
        before = self.snapshot()
        await view.confirm(self.request(owner=False))
        self.assertEqual(before, self.snapshot())
        await view.close(self.request())
        await view.confirm(self.request())
        self.assertEqual(before, self.snapshot())

    async def test_repaired_electricity_calls_existing_typed_ticket_service(self):
        self.missing_mapping('electricity')
        draft = await entries.preview(self.guild, self.owner, 'electricity')
        await entries.repair(self.guild, self.owner, draft, confirmed=True)
        item = dict(channel_id=None)
        with patch.object(tickets, 'open_ticket', AsyncMock(return_value=(item, True))) as create:
            request = self.request('electricity', owner=False)
            await controls.SupportOffers().electricity.callback(request)
        create.assert_awaited_once()
        self.assertEqual(create.call_args.kwargs['ticket_type'], 'ELECTRICITY_REQUEST')
        self.assertEqual(len(tickets.list_tickets(self.guild.id)), 0)

    async def test_recovery_failure_does_not_block_either_entry_refresh(self):
        with patch.object(tickets, 'recover', AsyncMock(side_effect=RuntimeError('synthetic recovery failure'))), \
             patch.object(tickets, 'refresh_entry', AsyncMock(side_effect=RuntimeError('synthetic support failure'))) as first, \
             patch.object(support, 'refresh_electricity_entry', AsyncMock()) as second:
            await controls.Tickets(SimpleNamespace()).reconcile(self.guild)
        first.assert_awaited_once_with(self.guild)
        second.assert_awaited_once_with(self.guild)

    async def test_electricity_refresh_independent_of_other_partner_mapping(self):
        spec, channel, message = self.boards['electricity']
        with patch.object(support, 'finish_household_migration', AsyncMock()) as retire:
            await support.sync_support_messages(self.guild)
        retire.assert_not_awaited()
        self.assertEqual(message.edits, 1)
        self.assertEqual(channel.sends, 0)
        self.assertEqual(db.get_setting(spec['key']), str(message.id))

    async def test_startup_never_creates_or_rebinds_missing_entries(self):
        for kind in entries.KINDS:
            self.missing_mapping(kind)
            spec, channel, message = self.boards[kind]
            refresh = tickets.refresh_entry if kind == 'support' else support.refresh_electricity_entry
            with self.assertRaises(ServerMessageError):
                await refresh(self.guild)
            self.assertEqual(db.get_setting(spec['key']), '')
            self.assertEqual(channel.sends, 0)
            self.assertEqual(message.edits, 0)

    async def test_deleted_message_not_recreated_on_refresh(self):
        spec, channel, message = self.boards['electricity']
        channel.messages.pop(message.id)
        with self.assertRaises(ServerMessageError):
            await support.refresh_electricity_entry(self.guild)
        self.assertEqual(channel.sends, 0)
        self.assertEqual(db.get_setting(spec['key']), str(message.id))

    async def test_repaired_entry_survives_handler_restart(self):
        self.missing_mapping()
        await entries.repair(self.guild, self.owner, await entries.preview(self.guild, self.owner, 'support'), confirmed=True)
        registered = []
        await controls.Tickets(SimpleNamespace(add_view=registered.append)).cog_load()
        request = self.request(owner=False)
        entry = next(v for v in registered if isinstance(v, controls.TicketEntry))
        await entry.create.callback(request)
        request.response.send_modal.assert_awaited_once()
        self.assertEqual(self.boards['support'][1].sends, 0)
