import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import test_onboarding as fixtures
from database import db
from services import ticket_service as tickets
from services import community_structure_service as structure
from services.server_setup_service import repair_server
from cogs.tickets import Tickets, TicketEntry, TicketActions, TicketModal, CloseConfirmation

class TicketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixtures.OnboardingTests.setUp(self)
        self.guild.owner_id=99
        self.members={}
        self.guild.get_member=lambda uid:self.members.get(uid)
        self.a=self.member(10);self.b=self.member(11);self.mod=self.member(20,True);self.other_mod=self.member(21,True)
        await repair_server(self.guild,self.bot)

    def member(self,uid,staff=False):
        member=MagicMock(spec=discord.Member)
        member.id=uid;member.guild=self.guild;member.bot=False
        member.roles=[self.guild.default_role]+([self.guild.mod] if staff else [])
        self.members[uid]=member
        return member

    async def create(self,member=None):
        return (await tickets.open_ticket(self.guild,member or self.a,'Voice issue','I cannot join my room.','Voice'))[0]

    def interaction(self,member,item):
        channel=self.guild.get_channel(item['channel_id'])
        result=SimpleNamespace(guild=self.guild,guild_id=self.guild.id,user=member,channel_id=channel.id,
            message=channel.messages[tickets.get(item['id'])['opening_message_id']],
            response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock(),edit_message=AsyncMock(),is_done=lambda:True),
            followup=SimpleNamespace(send=AsyncMock()))
        return result

    async def test_setup_identity_read_only_and_private_category(self):
        before=[c.id for c in self.guild.channels]
        await repair_server(self.guild,self.bot)
        self.assertEqual(before,[c.id for c in self.guild.channels])
        entry=tickets.mapped(self.guild,'need-support')
        self.assertIs(entry.category,self.start)
        self.assertEqual(entry.sends,1)
        self.assertFalse(entry.overwrites_for(self.guild.default_role).send_messages)
        self.assertTrue(entry.overwrites_for(self.guild.default_role).view_channel)
        tickets.check_private(tickets.mapped(self.guild,'support-tickets',True))
        tickets.check_private(tickets.mapped(self.guild,'ticket-logs'))
        self.assertNotIn(self.a,tickets.mapped(self.guild,'ticket-logs').overwrites)

    async def test_two_users_isolated_staff_can_see_both(self):
        for owner,other in ((self.a,self.b),(self.b,self.a)):
            item=await self.create(owner);channel=self.guild.get_channel(item['channel_id'])
            tickets.check_private(channel,owner.id)
            self.assertIsNone(channel.overwrites_for(other).view_channel)
            self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
            self.assertTrue(channel.overwrites_for(self.guild.mod).view_channel)
            own=channel.overwrites_for(owner)
            self.assertTrue(own.view_channel and own.send_messages and own.attach_files and own.embed_links)
            self.assertFalse(own.manage_channels or own.manage_roles or own.manage_messages)
            self.assertFalse(own.create_public_threads or own.create_private_threads)
            self.assertEqual(channel.name,f'ticket-{item["id"]:04d}')
            self.assertTrue(channel.messages[item['opening_message_id']].pinned)

    async def test_double_submit_and_configurable_limit(self):
        results=await asyncio.gather(*(tickets.open_ticket(self.guild,self.a,'Subject','Body') for _ in range(5)))
        self.assertEqual(sum(created for _,created in results),1)
        self.assertEqual(len(tickets.list_tickets(self.guild.id)),1)
        db.set_setting('ticket_open_limit:1',2)
        repeated=await tickets.open_ticket(self.guild,self.a,'Subject','Body')
        self.assertFalse(repeated[1])
        self.assertTrue((await tickets.open_ticket(self.guild,self.a,'Different','Body'))[1])
        self.assertFalse((await tickets.open_ticket(self.guild,self.a,'Third','Body'))[1])

    async def test_validation_required_fields_and_feature(self):
        for subject,description,feature in [('', 'body','Bot'),('subject',' ','Bot'),('x'*101,'body','Bot'),('subject','x'*901,'Bot'),('subject','body','bad')]:
            with self.assertRaises(ValueError): await tickets.open_ticket(self.guild,self.a,subject,description,feature)
        self.assertEqual(tickets.list_tickets(self.guild.id),[])
        modal=TicketModal()
        self.assertTrue(modal.subject.required and modal.description.required)
        self.assertFalse(modal.feature.required)

    async def test_staff_assignment_status_and_authorization(self):
        item=await self.create()
        for actor,action in [(self.a,'take'),(self.b,'close'),(self.a,'wait')]:
            with self.assertRaises(ValueError): await tickets.change(self.guild,actor,item['id'],action)
        with self.assertRaises(ValueError): await tickets.change(self.guild,self.mod,item['id'],'wait')
        taken=await tickets.change(self.guild,self.mod,item['id'],'take')
        self.assertEqual((taken['status'],taken['assigned_staff_id']),('IN_PROGRESS',self.mod.id))
        await tickets.change(self.guild,self.mod,item['id'],'take')
        with self.assertRaises(ValueError): await tickets.change(self.guild,self.other_mod,item['id'],'take')
        self.assertEqual((await tickets.change(self.guild,self.mod,item['id'],'wait'))['status'],'WAITING_FOR_USER')
        self.assertEqual((await tickets.change(self.guild,self.mod,item['id'],'take'))['status'],'IN_PROGRESS')

    async def test_close_requires_confirmation_keeps_history_and_durable_lock(self):
        item=await self.create();interaction=self.interaction(self.a,item)
        await TicketActions().act(interaction,'close')
        self.assertEqual(tickets.get(item['id'])['status'],'OPEN')
        confirmation=interaction.followup.send.call_args.kwargs['view']
        self.assertIsInstance(confirmation,CloseConfirmation)
        await confirmation.confirm.callback(interaction)
        closed=tickets.get(item['id']);self.assertEqual(closed['status'],'CLOSED')
        self.assertIsNotNone(closed['closed_at'])
        channel=self.guild.get_channel(closed['channel_id'])
        self.assertTrue(channel.overwrites_for(self.a).view_channel)
        self.assertFalse(channel.overwrites_for(self.a).send_messages)
        self.assertEqual(channel.sends,1)
        await tickets.recover(self.guild)
        self.assertEqual(tickets.get(item['id'])['status'],'CLOSED')
        await tickets.change(self.guild,self.a,item['id'],'close')
        with self.assertRaises(ValueError): await tickets.change(self.guild,self.mod,item['id'],'take')
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ticket_audit WHERE action='close'").fetchone()[0],1)
        self.assertGreaterEqual(tickets.mapped(self.guild,'ticket-logs').sends,2)

    async def test_staff_can_close_and_cancel_does_not_close(self):
        item=await self.create();interaction=self.interaction(self.mod,item)
        confirmation=CloseConfirmation(self.mod.id,item['id'])
        await confirmation.cancel.callback(interaction)
        self.assertEqual(tickets.get(item['id'])['status'],'OPEN')
        await tickets.change(self.guild,self.mod,item['id'],'close')
        self.assertEqual(tickets.get(item['id'])['status'],'CLOSED')

    async def test_restart_registers_controls_and_recovers_same_message(self):
        item=await self.create();await tickets.change(self.guild,self.mod,item['id'],'take')
        bot=SimpleNamespace(add_view=MagicMock())
        await Tickets(bot).cog_load()
        views=[c.args[0] for c in bot.add_view.call_args_list]
        self.assertTrue(all(v.is_persistent() for v in views))
        self.assertEqual(len(views),3)
        db.set_setting(f'ticket_opening:1:{item["id"]}','')
        await tickets.recover(self.guild)
        restored=tickets.get(item['id'])
        self.assertEqual(restored['opening_message_id'],item['opening_message_id'])
        self.assertEqual(restored['assigned_staff_id'],self.mod.id)
        self.assertEqual(self.guild.get_channel(item['channel_id']).sends,1)

    async def test_creator_leaves_and_staff_role_revoked(self):
        item=await self.create();await tickets.change(self.guild,self.mod,item['id'],'take')
        self.members.pop(self.a.id);self.mod.roles=[self.guild.default_role]
        await tickets.recover(self.guild)
        updated=tickets.get(item['id'])
        self.assertEqual(updated['creator_left'],1);self.assertIsNone(updated['assigned_staff_id'])
        await tickets.change(self.guild,self.other_mod,item['id'],'take')
        await tickets.change(self.guild,self.other_mod,item['id'],'close')

    async def test_uncertain_create_recovers_topic_without_duplicate(self):
        category=tickets.mapped(self.guild,'support-tickets',True)
        real=category.create_text_channel
        async def uncertain(*args,**kwargs):
            await real(*args,**kwargs)
            raise RuntimeError('Response lost')
        with patch.object(category,'create_text_channel',side_effect=uncertain):
            with self.assertRaises(RuntimeError): await self.create()
        self.assertEqual(len(category.text_channels),1)
        self.assertFalse((await tickets.open_ticket(self.guild,self.a,'Retry','Retry'))[1])
        await tickets.recover(self.guild)
        item=tickets.list_tickets(self.guild.id)[0]
        self.assertIsNotNone(item['channel_id']);self.assertIsNotNone(item['opening_message_id'])
        self.assertEqual(len(category.text_channels),1)

    async def test_failed_close_publish_remains_closed_and_recovery_locks(self):
        item=await self.create()
        with patch.object(tickets,'publish',side_effect=tickets.ServerMessageError('Failed')):
            with self.assertRaises(tickets.ServerMessageError): await tickets.change(self.guild,self.a,item['id'],'close')
        self.assertEqual(tickets.get(item['id'])['status'],'CLOSED')
        await tickets.recover(self.guild)
        self.assertFalse(self.guild.get_channel(item['channel_id']).overwrites_for(self.a).send_messages)

    async def test_forged_message_does_not_control_ticket(self):
        item=await self.create();interaction=self.interaction(self.mod,item)
        interaction.message=SimpleNamespace(id=123)
        await TicketActions().act(interaction,'take')
        self.assertEqual(tickets.get(item['id'])['status'],'OPEN')

    async def test_guide_health_and_worst_case_message_size(self):
        from services.health_service import scan
        text=structure.guide_text(self.guild)
        self.assertIn('## 🆘 Need Support?',text);self.assertIn('## 🤝 Partners & Benefits',text)
        self.assertLess(len(text)+200,2000)
        item=await self.create()
        item['subject']='@'*100;item['description']='*'*900
        self.assertLess(len(tickets.opening_text(item)),2000)
        findings=await scan(self.guild,messages=False)
        self.assertTrue(any(f.name=='Ticket System' and f.state=='PASS' for f in findings))
        channel=self.guild.get_channel(item['channel_id'])
        channel.overwrites[self.b]=discord.PermissionOverwrite(view_channel=True)
        findings=await scan(self.guild,messages=False)
        self.assertTrue(any(f.name=='Ticket System' and f.state=='MANUAL_REVIEW' for f in findings))

    async def test_staff_role_permission_revocation_locks_existing_history(self):
        item=await self.create()
        await tickets.change(self.guild,self.mod,item['id'],'take')
        self.guild.mod.permissions=discord.Permissions.none()
        await tickets.recover(self.guild)
        for channel in (tickets.mapped(self.guild,'support-tickets',True),tickets.mapped(self.guild,'ticket-logs'),self.guild.get_channel(item['channel_id'])):
            self.assertFalse(channel.overwrites_for(self.guild.mod).view_channel)
        self.assertIsNone(tickets.get(item['id'])['assigned_staff_id'])

    async def test_full_category_rejects_without_reserving_or_deleting(self):
        category=tickets.mapped(self.guild,'support-tickets',True)
        for number in range(50): self.guild.add_channel(f'retained-{number}',category)
        with self.assertRaises(tickets.ServerMessageError): await self.create()
        self.assertEqual(len(category.text_channels),50)
        self.assertEqual(tickets.list_tickets(self.guild.id),[])

    async def test_ticket_message_suppresses_mentions(self):
        item=await self.create()
        channel=self.guild.get_channel(item['channel_id'])
        message=channel.messages[item['opening_message_id']]
        with patch.object(message,'edit',wraps=message.edit) as edit:
            await tickets.publish(self.guild,item)
        mentions=edit.call_args.kwargs['allowed_mentions']
        self.assertFalse(mentions.everyone or mentions.roles or mentions.users)

    async def test_wrong_user_confirmation_and_wrong_guild_rejected(self):
        item=await self.create()
        interaction=self.interaction(self.b,item)
        confirm=CloseConfirmation(self.a.id,item['id'])
        await confirm.confirm.callback(interaction)
        self.assertEqual(tickets.get(item['id'])['status'],'OPEN')
        self.b.guild=SimpleNamespace(id=999)
        with self.assertRaises(ValueError): await tickets.change(self.guild,self.b,item['id'],'close')

    async def test_missing_channel_is_not_recreated_and_closed_stays_closed(self):
        item=await self.create()
        await tickets.change(self.guild,self.a,item['id'],'close')
        self.guild.text_channels.remove(self.guild.get_channel(item['channel_id']))
        before=[c.id for c in self.guild.channels]
        await tickets.recover(self.guild)
        self.assertEqual(before,[c.id for c in self.guild.channels])
        self.assertEqual(tickets.get(item['id'])['status'],'CLOSED')

if __name__=='__main__': unittest.main()
