"""Partner restructuring: identity, privacy, migration and retry safety."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
import discord
import test_onboarding as fixtures
from database import db
from services import support_service as support, community_structure_service as structure
from services.server_setup_service import repair_server
from services.server_service import ServerMessageError


class SupportTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    async def setup_board(self):
        changed, failed = await repair_server(self.guild,self.bot)
        self.assertFalse(any('support' in str(f).lower() or 'partner' in str(f).lower() for f in failed), failed)
        return support.resolve(self.guild,'channel')

    async def test_structure_repeated_setup_preserves_ids_and_read_only(self):
        channel=await self.setup_board()
        before=[c.id for c in self.guild.channels]
        await self.setup_board()
        self.assertEqual(before,[c.id for c in self.guild.channels])
        self.assertIs(channel.category,self.start)
        partners=support.resource(self.guild,'partners-benefits',True)
        self.assertEqual(partners.name,support.PARTNER_CATEGORY)
        for name in ['support-gamerhq',*support.PARTNER_CHANNELS]:
            ch=support.resource(self.guild,name)
            if name!='support-gamerhq':self.assertIs(ch.category,partners)
            rights=ch.overwrites_for(self.guild.default_role)
            self.assertTrue(rights.view_channel and rights.read_message_history)
            self.assertFalse(rights.send_messages or rights.create_public_threads or rights.create_private_threads)
            self.assertIsNot(rights.use_application_commands,False)
            for target in (self.guild.mod,self.guild.me):
                self.assertTrue(ch.overwrites_for(target).send_messages)
            expected=2 if name=='strom-gas' else 1
            self.assertEqual(ch.sends,expected)
            self.assertTrue(all(m.pinned for m in ch.messages.values()))

    async def test_renamed_channels_repaired_and_manual_content_untouched(self):
        await self.setup_board()
        target=support.resource(self.guild,'gaming-deals')
        identity=target.id
        target.name='renamed';target.category=self.community
        user=self.add_message(target,'Manual discussion',author=20)
        unrelated=self.guild.add_channel('my-channel',self.community)
        await self.setup_board()
        self.assertEqual(target.id,identity)
        self.assertEqual(target.name,support.PARTNER_CHANNELS['gaming-deals'])
        self.assertFalse(user.deleted)
        self.assertEqual(unrelated.edits,[])

    async def test_old_seven_messages_migrate_and_private_or_unrelated_messages_survive(self):
        channel=self.guild.add_channel(support.CHANNEL_NAME,self.start)
        db.set_setting(support.channel_key(self.guild),channel.id)
        old=[]
        headings=[support.TITLE,'## ℹ️ Transparency','## 🎮 Instant Gaming','## 🤖 PixVerse','## 🛒 Amazon','## 🇩🇪 For Germans','## 🎓 Strom & Gas Vertrieb']
        for section,heading in zip(support.LEGACY_SECTIONS,headings):
            msg=self.add_message(channel,heading+'\nOld copy',pinned=True)
            old.append(msg)
            db.set_setting(support.legacy_message_key(self.guild,section),msg.id)
        user=self.add_message(channel,'Unrelated',author=20)
        await self.setup_board()
        self.assertFalse(old[0].deleted)
        self.assertTrue(all(m.deleted for m in old[1:]))
        self.assertFalse(user.deleted)
        self.assertEqual(len(channel.messages),2)
        self.assertEqual(len(support.resource(self.guild,'strom-gas').messages),2)
        await self.setup_board()
        self.assertEqual(len(channel.messages),2)

    async def test_legacy_cleanup_waits_for_pins_and_retries_without_duplicates(self):
        channel=await self.setup_board()
        legacy=self.add_message(channel,'## 🇩🇪 For Germans\nLegacy',pinned=True)
        db.set_setting(support.legacy_message_key(self.guild,'energy'),legacy.id)
        germany=support.resource(self.guild,'finanzberatung')
        finance=germany.messages[int(db.get_setting(support.message_key(self.guild,'finance')))]
        finance.pinned=False
        error=discord.Forbidden(type('Response',(),{'status':403,'reason':'Forbidden'})(),'denied')
        with patch.object(finance,'pin',AsyncMock(side_effect=error)):
            result=await support.sync_support_messages(self.guild)
        self.assertEqual(result['pin_failures'],['finance'])
        self.assertFalse(legacy.deleted)
        with patch.object(legacy,'delete',AsyncMock(side_effect=error)):
            with self.assertRaises(discord.Forbidden):await support.sync_support_messages(self.guild)
        await support.sync_support_messages(self.guild)
        self.assertTrue(legacy.deleted)
        self.assertEqual(germany.sends,1)
        self.assertFalse(db.get_setting(support.legacy_message_key(self.guild,'energy')))

    async def test_interrupted_previous_reorder_is_retired(self):
        channel=await self.setup_board()
        old=self.add_message(channel,'## 🎮 Instant Gaming\nOld')
        staged=self.add_message(channel,'## 🛒 Amazon\nStaged')
        key=f'support_reorder:{self.guild.id}'
        db.set_setting(key,json.dumps({'channel':channel.id,'phase':'create','generation':'old','old':[old.id]}))
        db.set_setting(key+':old:amazon',staged.id)
        await support.sync_support_messages(self.guild)
        self.assertTrue(old.deleted and staged.deleted)
        self.assertFalse(db.get_setting(key))

    async def test_missing_middle_message_reorders_germany_once(self):
        await self.setup_board()
        channel=support.resource(self.guild,'strom-gas')
        await channel.messages[int(db.get_setting(support.message_key(self.guild,'energy')))].delete()
        await asyncio.gather(*(support.sync_support_messages(self.guild) for _ in range(3)))
        messages=sorted(channel.messages.values(),key=lambda m:m.id)
        self.assertEqual([m.content for m in messages],[text for _,text,_ in support.support_sections('strom-gas')])
        self.assertEqual(len(messages),2)
        self.assertTrue(all(m.pinned for m in messages))
        self.assertEqual(channel.sends,5)

    async def test_missing_id_recovers_pin_and_permission_error_does_not_repost(self):
        channel=await self.setup_board()
        mid=db.get_setting(support.message_key(self.guild))
        db.set_setting(support.message_key(self.guild),'')
        await support.sync_support_messages(self.guild)
        self.assertEqual(db.get_setting(support.message_key(self.guild)),mid)
        error=discord.Forbidden(type('Response',(),{'status':403,'reason':'Forbidden'})(),'denied')
        with patch.object(channel,'fetch_message',AsyncMock(side_effect=error)):
            with self.assertRaises(ServerMessageError):await support.sync_support_messages(self.guild)
        self.assertEqual(channel.sends,1)

    async def test_conflicting_or_protected_partner_channels_fail_closed(self):
        self.guild.add_channel('strom-gas',self.staff)
        before=[c.id for c in self.guild.channels]
        with self.assertRaises(ServerMessageError):await support.repair_support(self.guild,[])
        self.assertEqual(before,[c.id for c in self.guild.channels])
        self.guild.add_channel('strom-gas',self.community)
        with self.assertRaises(ServerMessageError):await support.repair_support(self.guild,[])

    async def test_empty_obsolete_category_removed_and_nonempty_preserved(self):
        await self.setup_board()
        category=self.guild.add_category('💜 SUPPORT GAMERHQ')
        ch=self.guild.add_channel('keep',category)
        result=await support.sync_support_messages(self.guild)
        self.assertEqual(result['retained_categories'],[category.id])
        ch.category=self.community
        await self.setup_board()
        self.assertNotIn(category,self.guild.categories)
        await self.setup_board()
        self.assertIsNone(support.resolve(self.guild,'category'))

    async def test_startup_requires_adopted_partner_channels(self):
        before=[c.id for c in self.guild.channels]
        self.assertIsNone(await support.refresh_support(self.guild))
        self.assertEqual(before,[c.id for c in self.guild.channels])

    async def test_copy_links_and_concise_guide(self):
        await self.setup_board()
        channel=support.resolve(self.guild,'channel')
        intro=next(iter(channel.messages.values())).content
        self.assertIn('keine zusätzlichen Kosten allein',intro)
        self.assertNotIn('Coming Soon',intro)
        self.assertNotIn('GamerHQ ist kostenlos nutzbar.',intro)
        self.assertIsNone(support.section_view('intro',None))
        self.assertIsNone(support.section_view('direct',None))
        for name in support.PARTNER_CHANNELS:self.assertIn(support.resource(self.guild,name).mention,intro)
        self.assertIn('Amazon',intro)
        for forbidden in ('Finanzcheck','PixVerse','Instant Gaming','PayPal'):
            self.assertNotIn(forbidden,intro)
        for _,text,_ in support.support_sections():self.assertLess(len(text),2000)
        self.assertNotIn('Provision',support.GERMANY_TEXT)
        self.assertNotIn('Empfehlungszugang',support.GERMANY_TEXT)
        self.assertIn('Dort erhältst du Zugang zum kostenlosen Kurs.',support.SALES_TEXT)
        self.assertNotIn('nächsten Schritte',support.SALES_TEXT)
        self.assertIn('Vermögensaufbau, Investments und Immobilien',support.FINANCE_TEXT)
        guide=structure.guide_text(self.guild)
        self.assertLess(len(guide)+200,2000)
        for section,_,entry in support.support_sections():
            if entry:
                view=support.section_view(section,entry)
                self.assertEqual(view.children[0].url,entry.url)
                self.assertNotIn(entry.url,guide)
        self.assertEqual([support.section_view(k,e).children[0].label for k,_,e in support.support_sections() if e],['🛒 Amazon öffnen','🎮 Open Instant Gaming','🤖 Open PixVerse'])

    def old_germany(self):
        category=self.guild.add_category(support.PARTNER_CATEGORY)
        channel=self.guild.add_channel('🇩🇪・germany-services',category)
        db.set_setting(support.channel_key(self.guild,'germany-services'),channel.id)
        messages={}
        for section,heading in [('energy','# ⚡ Strom & Gas'),('energy_sales','# 🎓 Strom & Gas Vertrieb'),('finance','# 💶 Finanzcheck & Planung')]:
            messages[section]=self.add_message(channel,heading+'\nOld managed copy',pinned=True)
            db.set_setting(support.message_key(self.guild,section),messages[section].id)
        return channel,messages

    async def test_reuse_germany_channel_preserves_energy_ids_and_moves_finance(self):
        channel,old=self.old_germany()
        user=self.add_message(channel,'Manual history',author=20)
        await self.setup_board()
        self.assertIs(support.resource(self.guild,'strom-gas'),channel)
        self.assertEqual(channel.name,'⚡・strom-gas')
        for key in ('energy','energy_sales'):
            self.assertEqual(db.get_setting(support.message_key(self.guild,key)),str(old[key].id))
            self.assertFalse(old[key].deleted)
        self.assertTrue(old['finance'].deleted)
        self.assertFalse(user.deleted)
        finance=support.resource(self.guild,'finanzberatung')
        self.assertEqual(len(finance.messages),1)
        self.assertIsNone(support.resource(self.guild,'germany-services'))
        self.assertIsNone(support.legacy_review_channel(self.guild))
        ids=[c.id for c in self.guild.channels]
        await self.setup_board()
        self.assertEqual(ids,[c.id for c in self.guild.channels])
        for text in (support.GERMANY_TEXT,support.SALES_TEXT,support.FINANCE_TEXT):
            self.assertIn('🇩🇪 Nur für Nutzer in Deutschland.',text)

    async def test_existing_strom_target_keeps_manual_legacy_history_for_review(self):
        channel,old=self.old_germany()
        target=self.guild.add_channel('⚡・strom-gas',channel.category)
        user=self.add_message(channel,'Manual history',author=20)
        await self.setup_board()
        self.assertIs(support.resource(self.guild,'strom-gas'),target)
        self.assertTrue(all(m.deleted for m in old.values()))
        self.assertFalse(user.deleted)
        self.assertIs(support.legacy_review_channel(self.guild),channel)
        from services.health_service import scan
        self.guild.get_member=lambda uid:None
        findings=await scan(self.guild)
        self.assertEqual(next(f.state for f in findings if f.name=='Legacy germany-services'),'MANUAL_REVIEW')
        await self.setup_board()
        self.assertEqual(len(target.messages),2)
        self.assertEqual(list(channel.messages),[user.id])

    async def test_split_cleanup_failure_retains_snapshot_and_retries(self):
        channel,old=self.old_germany()
        error=discord.Forbidden(type('Response',(),{'status':403,'reason':'Forbidden'})(),'denied')
        with patch.object(old['finance'],'delete',AsyncMock(side_effect=error)):
            with self.assertRaises(discord.Forbidden):await support.repair_support(self.guild,[])
        self.assertTrue(db.get_setting(f'partner_split:{self.guild.id}'))
        finance=support.resource(self.guild,'finanzberatung')
        self.assertEqual(finance.sends,1)
        await support.repair_support(self.guild,[])
        self.assertTrue(old['finance'].deleted)
        self.assertEqual(finance.sends,1)
        self.assertFalse(db.get_setting(f'partner_split:{self.guild.id}'))

    async def test_split_captures_interrupted_three_topic_reorder(self):
        channel,old=self.old_germany()
        key=f'partner_reorder:{self.guild.id}'
        extra=self.add_message(channel,'# 💶 Finanzcheck & Planung\nStaged old finance')
        db.set_setting(key,json.dumps({'channel':channel.id,'phase':'create','generation':'previous','old':[m.id for m in old.values()]}))
        db.set_setting(key+':previous:finance',extra.id)
        await self.setup_board()
        self.assertTrue(extra.deleted and old['finance'].deleted)
        self.assertEqual(len(channel.messages),2)
        self.assertFalse(db.get_setting(key))
