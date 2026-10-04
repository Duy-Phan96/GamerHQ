"""The public support button opens a private chat without required form input."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import discord
import test_tickets as fixtures
from cogs import tickets as controls
from database import db
from services import ticket_service as tickets, support_service as support


class SupportChatTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.TicketTests.asyncSetUp
    member = fixtures.TicketTests.member
    create = fixtures.TicketTests.create
    interaction = fixtures.TicketTests.interaction

    def entry_request(self, member=None, *, electricity=False):
        if electricity:
            channel = support.resource(self.guild, 'electricity')
            key = support.message_key(self.guild, 'household')
        else:
            channel = tickets.mapped(self.guild, 'need-support')
            key = f'ticket_entry:{self.guild.id}'
        message = channel.messages[int(db.get_setting(key))]
        return SimpleNamespace(guild=self.guild, guild_id=self.guild.id, user=member or self.a,
            channel_id=channel.id, message=message,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(),
                send_modal=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()))

    async def test_click_opens_chat_without_form_and_preserves_entry(self):
        request = self.entry_request()
        original = (request.message.id, request.message.content, request.message.edits)
        await controls.TicketEntry().create.callback(request)
        request.response.send_modal.assert_not_awaited()
        request.response.defer.assert_awaited_once_with(ephemeral=True)
        rows = tickets.list_tickets(self.guild.id)
        self.assertEqual(len(rows), 1)
        item = rows[0]
        self.assertEqual((item['ticket_type'], item['category']), ('GENERAL_SUPPORT', 'Other'))
        channel = self.guild.get_channel(item['channel_id'])
        self.assertEqual(channel.category_id, tickets.mapped(self.guild, 'support-tickets', True).id)
        tickets.check_private(channel, self.a.id)
        self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
        self.assertTrue(channel.overwrites_for(self.a).view_channel)
        self.assertTrue(channel.overwrites_for(self.a).send_messages)
        self.assertTrue(channel.overwrites_for(self.guild.mod).send_messages)
        self.assertIsNot(channel.overwrites_for(self.b).view_channel, True)
        text = channel.messages[item['opening_message_id']].content
        self.assertIn('Tell us what you need help with', text)
        self.assertIn('Assigned to: Not assigned', text)
        self.assertNotIn('**Subject:**', text)
        self.assertNotIn('**Feature:**', text)
        reply = request.followup.send.call_args
        self.assertTrue(reply.kwargs['ephemeral'])
        self.assertEqual(reply.kwargs['view'].children[0].label, 'Open Ticket')
        self.assertTrue(reply.kwargs['view'].children[0].url.endswith('/' + str(channel.id)))
        self.assertEqual(original, (request.message.id, request.message.content, request.message.edits))

    async def test_repeat_and_concurrent_clicks_reuse_existing_ticket(self):
        await asyncio.gather(*(controls.TicketEntry().create.callback(self.entry_request()) for _ in range(5)))
        rows = tickets.list_tickets(self.guild.id)
        self.assertEqual(len(rows), 1)
        request = self.entry_request()
        await controls.TicketEntry().create.callback(request)
        self.assertEqual(request.followup.send.call_args.kwargs['view'].children[0].label, 'Open Existing Ticket')
        self.assertEqual(len(tickets.mapped(self.guild, 'support-tickets', True).text_channels), 1)

    async def test_electricity_and_support_share_lifecycle_but_not_open_limit(self):
        await controls.TicketEntry().create.callback(self.entry_request())
        await controls.SupportOffers().electricity.callback(self.entry_request(electricity=True))
        rows = tickets.list_tickets(self.guild.id)
        self.assertEqual({item['ticket_type'] for item in rows}, tickets.ACTIVE_TICKET_TYPES)
        self.assertEqual(len(rows), 2)
        for item in rows:
            channel = self.guild.get_channel(item['channel_id'])
            original_message = item['opening_message_id']
            self.assertTrue(channel.messages[original_message].view.wait.disabled)
            await tickets.change(self.guild, self.mod, item['id'], 'take')
            self.assertIn(f'Assigned to: <@{self.mod.id}>', channel.messages[original_message].content)
            self.assertFalse(channel.messages[original_message].view.wait.disabled)
            self.assertTrue(channel.overwrites_for(self.a).send_messages)
            self.assertTrue(channel.overwrites_for(self.guild.mod).send_messages)
            await tickets.change(self.guild, self.mod, item['id'], 'wait')
            await tickets.change(self.guild, self.a, item['id'], 'close')
            self.assertEqual(channel.messages[original_message].view.children, [])
            self.assertTrue(channel.overwrites_for(self.a).view_channel)
            self.assertFalse(channel.overwrites_for(self.a).send_messages)
            self.assertEqual(tickets.get(item['id'])['opening_message_id'], original_message)
            self.assertEqual(channel.sends, 1)

    async def test_closed_old_controls_do_not_reopen_or_reassign(self):
        item, _ = await tickets.open_support_chat(self.guild, self.a)
        await tickets.change(self.guild, self.a, item['id'], 'close')
        request = self.interaction(self.mod, item)
        await controls.TicketActions().act(request, 'take')
        self.assertEqual(tickets.get(item['id'])['status'], 'CLOSED')
        self.assertIn('closed', request.followup.send.call_args.args[0])

    async def test_staff_claim_is_exclusive_and_regular_member_cannot_take(self):
        item, _ = await tickets.open_support_chat(self.guild, self.a)
        for actor in (self.a, self.b):
            with self.assertRaises(ValueError):
                await tickets.change(self.guild, actor, item['id'], 'take')
        outcomes = await asyncio.gather(*(tickets.change(self.guild, actor, item['id'], 'take')
            for actor in (self.mod, self.other_mod)), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in outcomes), 1)
        self.assertIn(tickets.get(item['id'])['assigned_staff_id'], (self.mod.id, self.other_mod.id))

    async def test_legacy_structured_ticket_keeps_submitted_details(self):
        item = await self.create()
        text = tickets.opening_text(item)
        self.assertIn('Voice issue', text)
        self.assertIn('I cannot join my room.', text)
        self.assertIn('**Feature:** Voice', text)
        again, created = await tickets.open_support_chat(self.guild, self.a)
        self.assertFalse(created)
        self.assertEqual(again['id'], item['id'])
        self.assertEqual(again['description'], item['description'])

    async def test_partial_creation_is_not_reported_as_ready(self):
        request = self.entry_request()
        await controls.reply_opened_ticket(request, {'channel_id': None}, True)
        self.assertIn('not confirmed ready', request.followup.send.call_args.args[0])
        self.assertIsNone(request.followup.send.call_args.kwargs['view'])
        self.assertEqual(tickets.list_tickets(self.guild.id), [])

    async def test_response_resolves_cache_miss_once_without_new_creation(self):
        item, _ = await tickets.open_support_chat(self.guild, self.a)
        request = self.entry_request()
        channel = self.guild.get_channel(item['channel_id'])
        original = self.guild.get_channel
        self.guild.get_channel = lambda cid: None if cid == channel.id else original(cid)
        self.guild.fetch_channel = AsyncMock(return_value=channel)
        await controls.reply_opened_ticket(request, item, True)
        self.guild.fetch_channel.assert_awaited_once_with(channel.id)
        self.assertIn('Open Ticket', [button.label for button in request.followup.send.call_args.kwargs['view'].children])
        self.assertEqual(len(tickets.list_tickets(self.guild.id)), 1)

    async def test_no_ticket_link_when_access_cannot_be_verified(self):
        item, _ = await tickets.open_support_chat(self.guild, self.a)
        request = self.entry_request()
        channel = self.guild.get_channel(item['channel_id'])
        channel.permissions_for = lambda member: discord.Permissions.none()
        await controls.reply_opened_ticket(request, item, False)
        self.assertIsNone(request.followup.send.call_args.kwargs['view'])
        self.assertIn('not confirmed ready', request.followup.send.call_args.args[0])

    async def test_restart_retains_ids_claims_and_closed_controls(self):
        item, _ = await tickets.open_support_chat(self.guild, self.a)
        await tickets.change(self.guild, self.mod, item['id'], 'take')
        second, _ = await tickets.open_support_chat(self.guild, self.b)
        await tickets.change(self.guild, self.b, second['id'], 'close')
        before = [(row['id'], row['channel_id'], row['opening_message_id']) for row in tickets.list_tickets(self.guild.id)]
        await tickets.recover(self.guild)
        registered = []
        await controls.Tickets(SimpleNamespace(add_view=registered.append)).cog_load()
        self.assertTrue(all(view.is_persistent() for view in registered))
        self.assertEqual(before, [(row['id'], row['channel_id'], row['opening_message_id']) for row in tickets.list_tickets(self.guild.id)])
        self.assertEqual(tickets.get(item['id'])['assigned_staff_id'], self.mod.id)
        channel = self.guild.get_channel(second['channel_id'])
        self.assertEqual(channel.messages[second['opening_message_id']].view.children, [])
