"""Household requests route to the existing private ticket lifecycle without external calls."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import test_tickets as fixtures
from cogs.tickets import SupportOffers, Tickets
from database import db
from services import support_service as support, ticket_service as tickets

class HouseholdRequestTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.TicketTests.asyncSetUp
    member = fixtures.TicketTests.member
    create = fixtures.TicketTests.create

    def request(self,member=None,kind='HOUSEHOLD_CHECK_REQUEST'):
        section={'HOUSEHOLD_CHECK_REQUEST':'household'}[kind]
        channel=support.resource(self.guild,support.section_channel(section))
        mid=int(db.get_setting(support.message_key(self.guild,section)))
        return SimpleNamespace(guild=self.guild,guild_id=self.guild.id,user=member or self.a,
            channel_id=channel.id,message=channel.messages[mid],
            response=SimpleNamespace(defer=AsyncMock(),is_done=lambda:True),followup=SimpleNamespace(send=AsyncMock()))

    async def test_only_household_entry_is_exposed(self):
        self.assertEqual([b.label for b in SupportOffers('household').children],['🔍 Haushaltscheck anfragen'])
        self.assertEqual([b.custom_id for b in SupportOffers().children],['gamerhq:offers:household-check'])
        for kind in ('ENERGY_SUPPORT','ENERGY_COURSE_REQUEST','FINANCE_REQUEST'):
            with self.assertRaises(ValueError):
                await tickets.open_ticket(self.guild,self.a,'Subject','Body',ticket_type=kind)
        self.assertEqual(tickets.list_tickets(self.guild.id),[])

    async def test_both_buttons_create_private_typed_tickets_and_staff_logs(self):
        for kind,method in [('HOUSEHOLD_CHECK_REQUEST','household_check')]:
            request=self.request(kind=kind)
            await getattr(SupportOffers(),method).callback(request)
            item=next(row for row in tickets.list_tickets(self.guild.id) if row['ticket_type']==kind)
            channel=self.guild.get_channel(item['channel_id'])
            tickets.check_private(channel,self.a.id)
            self.assertTrue(channel.overwrites_for(self.a).view_channel)
            self.assertTrue(channel.overwrites_for(self.guild.mod).view_channel)
            self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
            self.assertIsNone(channel.overwrites_for(self.b).view_channel)
            self.assertFalse(channel.overwrites_for(self.a).manage_channels)
            text=channel.messages[item['opening_message_id']].content
            self.assertTrue(text.startswith('# '+tickets.REQUEST_COPY[kind][0]+'\n'))
            self.assertIn(tickets.REQUEST_COPY[kind][1],text)
            self.assertIn('Status: OPEN',text)
            self.assertIn(tickets.TICKET_TYPES[kind],text)
            for phrase in ('Duy','chat with me','I will send you','technical support'):self.assertNotIn(phrase,text)
            logs=tickets.mapped(self.guild,'ticket-logs')
            self.assertTrue(any(tickets.TICKET_TYPES[kind] in m.content for m in logs.messages.values()))
            self.assertTrue(request.followup.send.call_args.kwargs['ephemeral'])

    async def test_per_type_limit_and_simultaneous_clicks(self):
        general=await self.create()
        for kind in ('HOUSEHOLD_CHECK_REQUEST',):
            await asyncio.gather(*(SupportOffers().request(self.request(kind=kind),kind) for _ in range(4)))
        rows=tickets.list_tickets(self.guild.id)
        self.assertEqual(len(rows),2)
        self.assertEqual({r['ticket_type'] for r in rows},tickets.ACTIVE_TICKET_TYPES)
        self.assertEqual(tickets.get(general['id'])['ticket_type'],'GENERAL_SUPPORT')
        response=self.request()
        await SupportOffers().request(response,'HOUSEHOLD_CHECK_REQUEST')
        self.assertIn('bereits eine offene Anfrage dieser Art',response.followup.send.call_args.args[0])
        self.assertEqual(len(tickets.list_tickets(self.guild.id)),2)

    async def test_existing_take_close_and_restart_preserve_type_and_identity(self):
        for kind in ('HOUSEHOLD_CHECK_REQUEST',):
            await SupportOffers().request(self.request(kind=kind),kind)
        before=tickets.list_tickets(self.guild.id)
        for item in before:
            await tickets.change(self.guild,self.mod,item['id'],'take')
            await tickets.change(self.guild,self.mod,item['id'],'wait')
            await tickets.change(self.guild,self.a,item['id'],'close')
            db.set_setting(f'ticket_opening:{self.guild.id}:{item["id"]}','')
        await tickets.recover(self.guild)
        for item in before:
            updated=tickets.get(item['id'])
            self.assertEqual(updated['ticket_type'],item['ticket_type'])
            self.assertEqual(updated['status'],'CLOSED')
            self.assertEqual(updated['opening_message_id'],item['opening_message_id'])
            channel=self.guild.get_channel(item['channel_id'])
            self.assertEqual(channel.sends,1)
            self.assertFalse(channel.overwrites_for(self.a).send_messages)
        with self.assertRaises(ValueError):await tickets.change(self.guild,self.b,before[0]['id'],'close')

    async def test_persistent_offers_registered_and_managed_pin_reused(self):
        bot=SimpleNamespace(add_view=MagicMock())
        await Tickets(bot).cog_load()
        views=[c.args[0] for c in bot.add_view.call_args_list]
        offers=next(v for v in views if isinstance(v,SupportOffers))
        self.assertTrue(offers.is_persistent())
        self.assertEqual([c.label for c in offers.children],['🔍 Haushaltscheck anfragen'])
        channel=support.resolve(self.guild,'channel');mid=int(db.get_setting(support.message_key(self.guild)))
        db.set_setting(support.message_key(self.guild),'')
        with patch.object(support,'upsert_fixed_message',wraps=support.upsert_fixed_message) as publish:
            await support.refresh_support(self.guild)
            await support.refresh_support(self.guild)
        self.assertEqual(publish.call_count,12)
        self.assertEqual([b.label for b in publish.call_args.kwargs['view'].children], ['🤖 Open PixVerse'])
        self.assertEqual(int(db.get_setting(support.message_key(self.guild))),mid)
        self.assertEqual(channel.sends,1)
        self.assertFalse(channel.overwrites_for(self.guild.default_role).send_messages)
        await offers.household_check.callback(self.request())
        self.assertEqual(tickets.list_tickets(self.guild.id)[0]['ticket_type'],'HOUSEHOLD_CHECK_REQUEST')

    async def test_stale_board_and_unknown_type_do_not_create(self):
        request=self.request();request.message=SimpleNamespace(id=123)
        await SupportOffers().request(request,'HOUSEHOLD_CHECK_REQUEST')
        self.assertEqual(tickets.list_tickets(self.guild.id),[])
        with self.assertRaises(ValueError):await tickets.open_ticket(self.guild,self.a,'Subject','Body',ticket_type='UNKNOWN')
        self.assertEqual(tickets.list_tickets(self.guild.id),[])

    async def test_ticket_button_rejects_intro_and_retired_type(self):
        request=self.request()
        await SupportOffers().request(request,'ENERGY_COURSE_REQUEST')
        self.assertEqual(tickets.list_tickets(self.guild.id),[])
        channel=support.resolve(self.guild,'channel')
        request.message=channel.messages[int(db.get_setting(support.message_key(self.guild)))]
        await SupportOffers().request(request,'HOUSEHOLD_CHECK_REQUEST')
        self.assertEqual(tickets.list_tickets(self.guild.id),[])

    async def test_historical_types_remain_readable_and_closable(self):
        for kind in ('ENERGY_SUPPORT','ENERGY_COURSE_REQUEST','FINANCE_REQUEST'):
            item=await self.create()
            with db.connect() as conn:
                conn.execute('UPDATE support_tickets SET ticket_type=? WHERE id=?',(kind,item['id']))
            await tickets.publish(self.guild,tickets.get(item['id']))
            await tickets.change(self.guild,self.a,item['id'],'close')
            await tickets.recover(self.guild)
            saved=tickets.get(item['id'])
            self.assertEqual(saved['ticket_type'],kind)
            self.assertEqual(saved['status'],'CLOSED')
            self.assertIsNotNone(self.guild.get_channel(saved['channel_id']))

    async def test_legacy_schema_migration_preserves_existing_closed_ticket(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(db,'DB_PATH',Path(folder)/'legacy.db'):
            old_schema=db.SCHEMA.replace("    ticket_type TEXT NOT NULL DEFAULT 'GENERAL_SUPPORT',\n",'',1)
            with db.connect() as conn:
                conn.executescript(old_schema)
                conn.execute("INSERT INTO support_tickets(id,guild_id,creator_discord_id,subject,description,created_at,updated_at,closed_at,status) VALUES(42,1,10,'Synthetic subject','Synthetic description',1,2,2,'CLOSED')")
            db.init_db();db.init_db()
            item=tickets.get(42)
            self.assertEqual(item['ticket_type'],'GENERAL_SUPPORT')
            self.assertEqual(item['status'],'CLOSED');self.assertEqual(item['closed_at'],2)
            self.assertEqual(item['description'],'Synthetic description')
            with db.connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM support_tickets').fetchone()[0],1)

if __name__=='__main__':unittest.main()
