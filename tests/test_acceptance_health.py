"""Failure/recovery acceptance tests with temporary SQLite and stateful Discord fakes."""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import test_onboarding as structural
import test_community_structure as suggestions_fixture
import test_lobby_management as lobby_fixture
import test_music_cleanup as music_fixture
from database import db
from services import health_service as health, community_structure_service as structure, lobby_service as lobby
from services.server_setup_service import repair_server
from cogs import suggestions
from cogs.server import ServerAdmin
from cogs.health import HealthView


class HealthTests(unittest.IsolatedAsyncioTestCase):
    setUp=structural.OnboardingTests.setUp
    add_message=structural.OnboardingTests.add_message

    async def asyncSetUp(self):
        self.guild.owner_id=90
        self.guild.get_member=lambda uid: None
        await repair_server(self.guild,self.bot)

    async def test_scan_core_healthy_and_read_only(self):
        with db.connect() as conn: before=list(map(tuple,conn.execute('SELECT * FROM settings')))
        edits=sum(len(c.edits) for c in self.guild.text_channels)
        result=await health.scan(self.guild)
        self.assertFalse(any(f.state=='CRITICAL' for f in result))
        for name in ('Database','guide pin','suggestions pin','bot-commands pin','Staff suggestions privacy','LFG persistence'):
            self.assertEqual(next(f.state for f in result if f.name==name),'PASS')
        with db.connect() as conn: self.assertEqual(before,list(map(tuple,conn.execute('SELECT * FROM settings'))))
        self.assertEqual(edits,sum(len(c.edits) for c in self.guild.text_channels))

    async def test_partner_health_and_registered_finance_handler(self):
        from cogs.tickets import SupportOffers
        fake=SimpleNamespace(persistent_views=[SupportOffers()],tree=SimpleNamespace(get_commands=lambda **kw:[]))
        result=await health.scan(self.guild,fake)
        for name in ('PARTNERS & BENEFITS','germany-services','gaming-deals','ai-tools','Partner ticket handlers'):
            self.assertEqual(next(f.state for f in result if f.name==name),'PASS')
        fake.persistent_views=[SupportOffers('energy')]
        result=await health.scan(self.guild,fake)
        self.assertEqual(next(f.state for f in result if f.name=='Partner ticket handlers'),'WARN')

    async def test_renamed_guide_deleted_pin_repaired_without_duplicates(self):
        guide=structure.core_channel(self.guild,'guide')
        old_id=guide.id
        guide.name='my-renamed-guide'
        for message in list(guide.messages.values()): await message.delete()
        findings=await health.scan(self.guild)
        self.assertTrue(any(f.name=='guide' and f.state=='REPAIRABLE' for f in findings))
        self.assertTrue(any(f.name=='guide pin' and f.state=='REPAIRABLE' for f in findings))
        unknown=self.guild.add_channel('personal-notes',self.community)
        await repair_server(self.guild,self.bot)
        await repair_server(self.guild,self.bot)
        self.assertEqual(guide.id,old_id); self.assertEqual(guide.name,'📘・guide')
        self.assertEqual(len(guide.messages),1)
        self.assertEqual(unknown.edits,[])

    async def test_old_message_mapping_recovers_renamed_channel(self):
        guide=structure.core_channel(self.guild,'guide')
        db.set_setting(structure.resource_key(self.guild,'guide'),'')
        guide.name='renamed-before-registry'
        await repair_server(self.guild,self.bot)
        self.assertEqual(guide.name,'📘・guide')
        self.assertEqual(guide.sends,1)

    async def test_missing_area_role_and_unknown_resources_need_manual_review(self):
        game=db.upsert_custom_game('Missing','🎮','Test')
        self.guild.get_role=lambda rid:None
        with db.connect() as conn: conn.execute('UPDATE games SET role_id=999,category_id=998,area_enabled=1 WHERE id=?',(game['id'],))
        unknown=self.guild.add_category('Unmapped category')
        findings=await health.scan(self.guild)
        self.assertTrue(any(f.name=='Game Area' and f.state=='MANUAL_REVIEW' for f in findings))
        self.assertTrue(any(f.name=='Game role' and f.state=='MANUAL_REVIEW' for f in findings))
        self.assertIn(unknown,self.guild.categories)

    async def test_health_and_details_deny_normal_user(self):
        actor=SimpleNamespace(id=20,guild_permissions=discord.Permissions())
        interaction=SimpleNamespace(guild=self.guild,user=actor,response=SimpleNamespace(send_message=AsyncMock()))
        await ServerAdmin.health.callback(ServerAdmin(self.bot),interaction)
        interaction.response.send_message.assert_awaited_once()
        self.assertFalse(await HealthView(self.guild,20,[]).interaction_check(interaction))

    async def test_storage_failure_is_critical_without_repair(self):
        import sqlite3
        with patch.object(db,'connect',side_effect=sqlite3.OperationalError('test')):
            result=await health.scan(self.guild)
        self.assertEqual(result[0].state,'CRITICAL')
        self.assertNotIn('test',result[0].detail)


class SuggestionHardeningTests(unittest.IsolatedAsyncioTestCase):
    setUp=structural.OnboardingTests.setUp
    asyncSetUp=suggestions_fixture.SuggestionTests.asyncSetUp

    async def test_concurrent_duplicate_submit_posts_once(self):
        await asyncio.gather(*(suggestions.submit(self.interaction,'Same title','Same idea') for _ in range(2)))
        self.inbox.send.assert_awaited_once()
        with db.connect() as conn: self.assertEqual(conn.execute('SELECT count(*) FROM suggestions').fetchone()[0],1)

    async def test_invalid_transition_and_terminal_status(self):
        await suggestions.submit(self.interaction,'Title','Idea')
        self.interaction.user.roles.append(self.guild.mod)
        view=suggestions.StaffSuggestionView()
        await view.change_status(self.interaction,'IMPLEMENTED')
        self.assertEqual(suggestions.get_suggestion(777,1)['status'],'NEW')
        await view.change_status(self.interaction,'ACCEPTED')
        await view.change_status(self.interaction,'IMPLEMENTED')
        await view.change_status(self.interaction,'REVIEWING')
        self.assertEqual(suggestions.get_suggestion(777,1)['status'],'IMPLEMENTED')


class LobbyHardeningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        patcher=patch.object(db,'DB_PATH',Path(temp.name)/'test.db');patcher.start();self.addCleanup(patcher.stop);db.init_db()
        game=db.upsert_custom_game('Test','🎮','Test')
        self.event=db.create_lfg_event(guild_id=1,game_id=game['id'],host_id=10,title='Test',start_at=int(time.time())+3600,max_players=3,invite_lead_minutes=15)['id']

    def test_offline_expired_join_and_proposal_cannot_revive_lobby(self):
        lobby.join(self.event,1,20)
        proposal=lobby.propose(self.event,1,20,int(time.time())+7200)
        with db.connect() as conn: conn.execute('UPDATE lfg_events SET start_at=? WHERE id=?',(int(time.time())-21601,self.event))
        with self.assertRaises(ValueError):lobby.join(self.event,1,21)
        with self.assertRaises(ValueError):lobby.decide(self.event,1,10,proposal,'ACCEPTED')

    def test_accept_once_expires_competing_proposals_and_cancel_blocks(self):
        lobby.join(self.event,1,20)
        first=lobby.propose(self.event,1,20,int(time.time())+7200)
        second=lobby.propose(self.event,1,20,int(time.time())+10800)
        lobby.decide(self.event,1,10,first,'ACCEPTED')
        for pid in (first,second):
            with self.assertRaises(ValueError):lobby.decide(self.event,1,10,pid,'ACCEPTED')
        third=lobby.propose(self.event,1,20,int(time.time())+14400)
        lobby.end(self.event,1,10,'cancelled')
        with self.assertRaises(ValueError):lobby.decide(self.event,1,10,third,'ACCEPTED')


class MusicHealthTests(unittest.IsolatedAsyncioTestCase):
    setUp=music_fixture.MusicCleanupTests.setUp
    role=music_fixture.MusicCleanupTests.role
    channel=music_fixture.MusicCleanupTests.channel
    link_game=music_fixture.MusicCleanupTests.link_game

    async def test_music_diagnostics_do_not_adopt_role_or_apply_permissions(self):
        from services.music_bot_service import role_key
        self.guild.text_channels=[self.chat]
        self.guild.members=[]
        self.guild.get_member.return_value=None
        self.assertIsNone(db.get_setting(role_key(self.guild)))
        findings=await health.scan(self.guild,messages=False)
        self.assertTrue(any(f.name=='Music Bots permissions' and f.state=='REPAIRABLE' for f in findings))
        self.assertIsNone(db.get_setting(role_key(self.guild)))
        self.chat.set_permissions.assert_not_awaited()


if __name__=='__main__':unittest.main()
