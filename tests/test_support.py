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

    async def test_navigation_mentions_are_unique_and_inside_their_bullets(self):
        channel = await self.setup_board()
        text = channel.messages[int(db.get_setting(support.message_key(self.guild)))].content
        lines = text.splitlines()
        for name, emoji, label in support.PARTNER_NAVIGATION:
            mention = support.resource(self.guild, name).mention
            self.assertIn(f'- {emoji} {mention} — {label}', lines)
            self.assertEqual(text.count(mention), 1)
        self.assertTrue(all(line.startswith('- ') for line in lines if '<#' in line))
        self.assertIn('**PARTNERS & BENEFITS**', text)
        self.assertTrue(text.endswith(support.DISCLOSURE))
        self.assertLess(len(text), 1000)

    async def test_persisted_identity_wins_over_name_only_lookalike(self):
        channel = await self.setup_board()
        actual = support.resource(self.guild, 'amazon')
        actual.name = 'renamed-partner'
        lookalike = self.guild.add_channel('🛒・amazon', self.community)
        await support.sync_support_messages(self.guild)
        text = channel.messages[int(db.get_setting(support.message_key(self.guild)))].content
        self.assertIn(actual.mention, text)
        self.assertNotIn(lookalike.mention, text)

    async def test_missing_mapping_omits_mention_and_health_requires_repair(self):
        from services.health_service import scan
        channel = await self.setup_board()
        target = support.resource(self.guild, 'amazon')
        mid = int(db.get_setting(support.message_key(self.guild)))
        db.set_setting(support.channel_key(self.guild, 'amazon'), '')
        self.assertIsNone(await support.sync_support_messages(self.guild))
        text = channel.messages[mid].content
        self.assertNotIn(target.mention, text)
        self.assertNotIn('#amazon', text)
        self.assertIn('More partner channels are being set up.', text)
        findings = await scan(self.guild, messages=False)
        self.assertEqual(next(f.state for f in findings if f.name == 'amazon'), 'REPAIRABLE')
        await self.setup_board()
        self.assertIn(target.mention, channel.messages[mid].content)
        self.assertEqual(channel.sends, 1)

    async def test_deleted_channel_is_not_rendered_and_unrelated_pins_survive(self):
        channel = await self.setup_board()
        target = support.resource(self.guild, 'ai-tools')
        self.guild.text_channels.remove(target)
        user = self.add_message(channel, 'Unrelated user pin', author=20, pinned=True)
        bot = self.add_message(channel, 'Unrelated bot pin', pinned=True)
        mid = int(db.get_setting(support.message_key(self.guild)))
        await support.sync_support_messages(self.guild)
        self.assertNotIn(target.mention, channel.messages[mid].content)
        await self.setup_board()
        await self.setup_board()
        self.assertEqual(channel.sends, 1)
        self.assertFalse(user.deleted or bot.deleted)
        self.assertEqual(user.edits + bot.edits, 0)

    def test_readme_documents_partner_structure(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8-sig')
        structure_block = text.split('```text', 1)[1].split('```', 1)[0]
        self.assertIn('PARTNERS & BENEFITS', structure_block)
        for name in support.PARTNER_CHANNELS:self.assertIn(name, structure_block)

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
            expected=1
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
        self.assertEqual(len(support.resource(self.guild,'haushaltscheck').messages),1)
        await self.setup_board()
        self.assertEqual(len(channel.messages),2)

    async def test_legacy_cleanup_waits_for_pins_and_retries_without_duplicates(self):
        channel=await self.setup_board()
        legacy=self.add_message(channel,'## 🇩🇪 For Germans\nLegacy',pinned=True)
        db.set_setting(support.legacy_message_key(self.guild,'energy'),legacy.id)
        germany=support.resource(self.guild,'haushaltscheck')
        finance=germany.messages[int(db.get_setting(support.message_key(self.guild,'household')))]
        finance.pinned=False
        error=discord.Forbidden(type('Response',(),{'status':403,'reason':'Forbidden'})(),'denied')
        with patch.object(finance,'pin',AsyncMock(side_effect=error)):
            result=await support.sync_support_messages(self.guild)
        self.assertEqual(result['pin_failures'],['household'])
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

    async def test_missing_household_message_recovers_once(self):
        await self.setup_board()
        channel=support.resource(self.guild,'haushaltscheck')
        await channel.messages[int(db.get_setting(support.message_key(self.guild,'household')))].delete()
        await asyncio.gather(*(support.sync_support_messages(self.guild) for _ in range(3)))
        self.assertEqual(len(channel.messages),1)
        self.assertEqual(channel.sends,2)
        self.assertTrue(next(iter(channel.messages.values())).pinned)

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
        self.assertIn(support.DISCLOSURE,intro)
        self.assertNotIn('keine zusätzlichen Kosten allein',intro)
        self.assertNotIn('eine Provision erhalten',intro)
        self.assertNotIn('Coming Soon',intro)
        self.assertNotIn('GamerHQ ist kostenlos nutzbar.',intro)
        self.assertIsNone(support.section_view('intro',None))
        self.assertIsNone(support.section_view('direct',None))
        for name in support.PARTNER_CHANNELS:self.assertIn(support.resource(self.guild,name).mention,intro)
        self.assertIn('Amazon',intro)
        for forbidden in ('Finanzcheck','PixVerse','Instant Gaming','PayPal'):
            self.assertNotIn(forbidden,intro)
        for _,text,_ in support.support_sections():self.assertLess(len(text),2000)
        self.assertIn('Nur für Nutzer in Deutschland.',support.HOUSEHOLD_TEXT)
        self.assertIn('mehrere passende Tarife',support.HOUSEHOLD_TEXT)
        self.assertIn('als PDF zum Vergleichen',support.HOUSEHOLD_TEXT)
        for phrase in ('Kurs', 'Finanzberatung', 'Investments', 'garantiert', 'besten Preis'):
            self.assertNotIn(phrase, support.HOUSEHOLD_TEXT)
        guide=structure.guide_text(self.guild)
        self.assertLess(len(guide)+200,2000)
        for section,_,entry in support.support_sections():
            if entry:
                view=support.section_view(section,entry)
                self.assertEqual(view.children[0].url,entry.url)
                self.assertNotIn(entry.url,guide)
        self.assertEqual([support.section_view(k,e).children[0].label for k,_,e in support.support_sections() if e],['🛒 Open Amazon','🎮 Open Instant Gaming','🤖 Open PixVerse'])

    def old_germany(self):
        category=self.guild.add_category(support.PARTNER_CATEGORY)
        channel=self.guild.add_channel('🇩🇪・germany-services',category)
        db.set_setting(support.channel_key(self.guild,'germany-services'),channel.id)
        messages={}
        for section,heading in [('energy','# ⚡ Strom & Gas'),('energy_sales','# 🎓 Strom & Gas Vertrieb'),('finance','# 💶 Finanzcheck & Planung')]:
            messages[section]=self.add_message(channel,heading+'\nOld managed copy',pinned=True)
            db.set_setting(support.message_key(self.guild,section),messages[section].id)
        return channel,messages

    async def test_reuse_legacy_channel_preserves_history_and_retires_old_flows(self):
        channel,old=self.old_germany()
        user=self.add_message(channel,'Manual history',author=20,pinned=True)
        unknown=self.add_message(channel,'# 🎓 Strom & Gas Vertrieb\nUnmapped manual bot pin',pinned=True)
        await self.setup_board()
        self.assertIs(support.resource(self.guild,'haushaltscheck'),channel)
        self.assertEqual(channel.name,'🇩🇪・haushaltscheck')
        self.assertTrue(all(m.deleted for m in old.values()))
        self.assertFalse(user.deleted or unknown.deleted)
        self.assertEqual(user.edits + unknown.edits,0)
        self.assertIn(channel,support.legacy_review_channels(self.guild))
        ids=[c.id for c in self.guild.channels]
        await self.setup_board()
        self.assertEqual(ids,[c.id for c in self.guild.channels])
        self.assertEqual(channel.sends,1)

    async def test_existing_household_target_keeps_manual_legacy_history_for_review(self):
        channel,old=self.old_germany()
        target=self.guild.add_channel('🇩🇪・haushaltscheck',channel.category)
        user=self.add_message(channel,'Manual history',author=20)
        await self.setup_board()
        self.assertIs(support.resource(self.guild,'haushaltscheck'),target)
        self.assertTrue(all(m.deleted for m in old.values()))
        self.assertFalse(user.deleted)
        self.assertIs(support.legacy_review_channel(self.guild),channel)
        from services.health_service import scan
        self.guild.get_member=lambda uid:None
        findings=await scan(self.guild)
        self.assertEqual(next(f.state for f in findings if f.name=='Legacy partner channels'),'MANUAL_REVIEW')
        await self.setup_board()
        self.assertEqual(len(target.messages),1)
        self.assertEqual(list(channel.messages),[user.id])

    async def test_cleanup_failure_retains_snapshot_and_retries(self):
        channel,old=self.old_germany()
        error=discord.Forbidden(type('Response',(),{'status':403,'reason':'Forbidden'})(),'denied')
        with patch.object(old['finance'],'delete',AsyncMock(side_effect=error)):
            with self.assertRaises(discord.Forbidden):await support.repair_support(self.guild,[])
        self.assertTrue(db.get_setting(f'household_migration:{self.guild.id}'))
        target=support.resource(self.guild,'haushaltscheck')
        self.assertEqual(target.sends,1)
        await support.repair_support(self.guild,[])
        self.assertTrue(old['finance'].deleted)
        self.assertEqual(target.sends,1)
        self.assertFalse(db.get_setting(f'household_migration:{self.guild.id}'))

    async def test_migration_captures_interrupted_three_topic_reorder(self):
        channel,old=self.old_germany()
        key=f'partner_reorder:{self.guild.id}'
        extra=self.add_message(channel,'# 💶 Finanzcheck & Planung\nStaged old finance')
        db.set_setting(key,json.dumps({'channel':channel.id,'phase':'create','generation':'previous','old':[m.id for m in old.values()]}))
        db.set_setting(key+':previous:finance',extra.id)
        await self.setup_board()
        self.assertTrue(extra.deleted and old['finance'].deleted)
        self.assertEqual(len(channel.messages),1)
        self.assertFalse(db.get_setting(key))

    async def test_custom_legacy_pin_is_preserved_and_retired_from_editor(self):
        from services import managed_message_service as managed
        channel,old=self.old_germany()
        msg=old['energy_sales']
        key=support.message_key(self.guild,'energy_sales')
        state=dict(key=key,guild_id=self.guild.id,channel_id=channel.id,message_id=msg.id,
                   content=msg.content,content_hash=managed.digest(msg.content),customized=True)
        managed.store(state)
        await self.setup_board()
        self.assertFalse(msg.deleted)
        self.assertEqual(msg.edits,0)
        self.assertTrue(managed.load(key)['retired'])
        self.assertNotIn(key,[s['key'] for s in managed.records(self.guild)])
        self.assertIn(channel,support.legacy_review_channels(self.guild))

    async def test_separate_strom_and_finance_migrate_without_deleting_manual_content(self):
        channel,old=self.old_germany()
        channel.name='⚡・strom-gas'
        db.set_setting(support.channel_key(self.guild,'germany-services'),'')
        db.set_setting(support.channel_key(self.guild,'strom-gas'),channel.id)
        finance=self.guild.add_channel('💶・finanzberatung',channel.category)
        db.set_setting(support.channel_key(self.guild,'finanzberatung'),finance.id)
        msg=old['finance'];channel.messages.pop(msg.id);finance.messages[msg.id]=msg;msg.channel=finance
        manual=self.add_message(finance,'Keep me',author=20,pinned=True)
        await self.setup_board()
        self.assertIs(support.resource(self.guild,'haushaltscheck'),channel)
        self.assertTrue(msg.deleted)
        self.assertFalse(manual.deleted)
        self.assertIn(finance,support.legacy_review_channels(self.guild))
        finance.category=self.staff
        await self.setup_board()
        self.assertIs(finance.category,self.staff)

    def test_partner_copy_matches_permanent_docs_and_exact_affiliate_links(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        document=(root/'docs/PARTNERS.md').read_text(encoding='utf-8')
        for section,content,_ in support.support_sections():
            if section!='intro':
                self.assertIn('```text\n'+content+'\n```',document)
        self.assertEqual([a.url for a in support.AFFILIATES],[
            'https://www.instant-gaming.com/?igr=gamer-0a9671a',
            'https://motivaiprivatelimited.sjv.io/c/7668488/3811144/49478',
            'https://amzn.to/4dnxPXh'])
        external=(root/'docs/INSTANT_GAMING.md').read_text(encoding='utf-8')
        for value in ('INSTANT_GAMING_BOT_ID','Marketing campaigns','Purchase notification','Buyer ranking','disabled'):
            self.assertIn(value,external)

    async def test_english_defaults_disclosure_and_german_household(self):
        await self.setup_board()
        intro=support.resource(self.guild,'support-gamerhq')
        self.assertIs(intro.category,self.start)
        text=intro.messages[int(db.get_setting(support.message_key(self.guild)))].content
        self.assertIn("Looking for useful deals, tools or services?",text)
        self.assertIn("selected offers and resources",text)
        self.assertNotIn('finanzberatung',text)
        self.assertIn("Want to support GamerHQ directly?",support.DIRECT_TEXT)
        self.assertIn('`Ctrl + D`',support.AMAZON_TEXT)
        self.assertNotIn('automatically',support.AMAZON_TEXT)
        self.assertEqual(support.GAMING_TEXT,'# 🎮 Gaming Deals\n\nFind current gaming deals, promotions and releases here.\n\nAffiliate / referral link')
        for section,content,affiliate in support.support_sections():
            if affiliate:
                self.assertTrue(content.endswith('Affiliate / referral link'))
                self.assertEqual(content.count('Affiliate / referral link'),1)
                for word in ('Discord','integration','configuration','scraper','öffnen'):
                    self.assertNotIn(word,content)
            if section!='household':
                for word in ('Wenn ', 'Hier findest', 'unterstützt', 'Tipp:', 'Du kannst'):
                    self.assertNotIn(word,content)
        self.assertIn('Nur für Nutzer in Deutschland.',support.HOUSEHOLD_TEXT)

    async def test_completed_legacy_mappings_retire_without_losing_review_identity(self):
        channel,old=self.old_germany()
        target=self.guild.add_channel('🇩🇪・haushaltscheck',channel.category)
        manual=self.add_message(channel,'Manual history',author=20)
        await self.setup_board()
        self.assertFalse(db.get_setting(support.channel_key(self.guild,'germany-services')))
        self.assertEqual(db.get_setting(f'retired_partner_channel:{self.guild.id}:germany-services'),str(channel.id))
        for section,message in old.items():
            self.assertFalse(db.get_setting(support.message_key(self.guild,section)))
            self.assertEqual(db.get_setting(f'retired_partner_message:{self.guild.id}:{section}'),str(message.id))
        channel.name='Owner archive'
        self.assertIn(channel,support.legacy_review_channels(self.guild))
        await self.setup_board()
        self.assertFalse(manual.deleted)
        self.assertEqual(target.sends,1)

    async def test_existing_completed_migration_cleans_stale_finance_mapping(self):
        await self.setup_board()
        finance=self.guild.add_channel('💶・finanzberatung',self.community)
        manual=self.add_message(finance,'Keep this',author=20,pinned=True)
        db.set_setting(support.channel_key(self.guild,'finanzberatung'),finance.id)
        db.set_setting(support.message_key(self.guild,'finance'),manual.id)
        await self.setup_board()
        self.assertFalse(db.get_setting(support.channel_key(self.guild,'finanzberatung')))
        self.assertFalse(db.get_setting(support.message_key(self.guild,'finance')))
        self.assertEqual(manual.edits,0)
        self.assertFalse(manual.deleted)
        self.assertIn(finance,support.legacy_review_channels(self.guild))
        from services.health_service import scan
        self.guild.get_member=lambda uid:None
        findings=await scan(self.guild)
        self.assertNotIn('finanzberatung',[f.name for f in findings])
        self.assertEqual(next(f.state for f in findings if f.name=='Legacy partner channels'),'MANUAL_REVIEW')

    async def test_unknown_legacy_mapping_is_preserved_for_review(self):
        channel,old=self.old_germany()
        old['energy'].content='Unrelated manual copy'
        await self.setup_board()
        self.assertFalse(old['energy'].deleted)
        self.assertEqual(old['energy'].edits,0)
        self.assertIn(channel,support.legacy_review_channels(self.guild))

    async def test_household_pin_failure_preserves_legacy_until_retry(self):
        channel,old=self.old_germany()
        original=support.pin_managed_message
        async def pin(message,**kwargs):
            if message.content==support.HOUSEHOLD_TEXT:
                raise discord.Forbidden(type('Response',(),{'status':403,'reason':'Forbidden'})(),'denied')
            await original(message,**kwargs)
        with patch.object(support,'pin_managed_message',side_effect=pin):
            with self.assertRaises(ServerMessageError):
                await support.repair_support(self.guild,[])
        self.assertTrue(all(not msg.deleted for msg in old.values()))
        self.assertTrue(db.get_setting(f'household_migration:{self.guild.id}'))
        await support.repair_support(self.guild,[])
        self.assertTrue(all(msg.deleted for msg in old.values()))
        self.assertEqual(channel.sends,1)

    async def test_external_bot_permission_is_scoped_and_survives_repair(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        external=MagicMock(id=4321,bot=True)
        self.guild.get_member=lambda uid:external if uid==4321 else None
        with patch('config.INSTANT_GAMING_BOT_ID',4321):
            await self.setup_board()
            await self.setup_board()
        for name in support.PARTNER_CHANNELS:
            rights=support.resource(self.guild,name).overwrites_for(external)
            self.assertEqual(rights.send_messages is True, name=='gaming-deals')
        rights=support.resource(self.guild,'gaming-deals').overwrites_for(external)
        self.assertFalse(rights.manage_channels or rights.manage_roles or rights.manage_messages or rights.mention_everyone)

    async def test_finance_cleanup_only_explicit_repair_and_health_read_only(self):
        from services.health_service import scan
        await self.setup_board()
        finance=self.guild.add_channel('💶・finanzberatung',self.community)
        db.set_setting(support.channel_key(self.guild,'finanzberatung'),finance.id)
        self.guild.get_member=lambda uid:None
        findings=await scan(self.guild)
        self.assertTrue(any(f.state=='REPAIRABLE' and 'finanzberatung' in f.detail for f in findings))
        await support.sync_support_messages(self.guild)
        self.assertIs(self.guild.get_channel(finance.id),finance)
        changed=[]
        await support.repair_support(self.guild,changed)
        self.assertIsNone(self.guild.get_channel(finance.id))
        self.assertIn('Deleted legacy channels: ✅ finanzberatung',changed)
        self.assertFalse(db.get_setting(support.channel_key(self.guild,'finanzberatung')))
        await support.repair_support(self.guild,[])

    async def test_finance_known_recorded_content_can_be_deleted(self):
        await self.setup_board()
        finance=self.guild.add_channel('💶・finanzberatung',self.community)
        message=self.add_message(finance,'# 💶 Finanzberatung\nLegacy default',pinned=True)
        db.set_setting(support.channel_key(self.guild,'finanzberatung'),finance.id)
        db.set_setting(support.message_key(self.guild,'finance'),message.id)
        await support.repair_support(self.guild,[])
        self.assertIsNone(self.guild.get_channel(finance.id))
        self.assertFalse(db.get_setting(support.message_key(self.guild,'finance')))

    async def test_finance_manual_content_and_uncertain_identity_have_exact_reasons(self):
        from services import legacy_finance_service as finance_service
        await self.setup_board()
        finance=self.guild.add_channel('💶・finanzberatung',self.community)
        self.assertEqual(await finance_service.inspect(self.guild,finance),'managed channel identity is not recorded')
        db.set_setting(support.channel_key(self.guild,'finanzberatung'),finance.id)
        manual=self.add_message(finance,'Keep my history',author=20)
        changed=[]
        await support.repair_support(self.guild,changed)
        self.assertIn('MANUAL_REVIEW: ⚠️ finanzberatung — contains unexpected/manual content',changed)
        self.assertFalse(manual.deleted)
        self.assertIs(self.guild.get_channel(finance.id),finance)

    async def test_finance_threads_dependencies_and_permissions_block_deletion(self):
        from services import legacy_finance_service as finance_service
        from types import SimpleNamespace
        await self.setup_board()
        finance=self.guild.add_channel('💶・finanzberatung',self.community)
        db.set_setting(support.channel_key(self.guild,'finanzberatung'),finance.id)
        finance.archived=[SimpleNamespace(id=999)]
        self.assertEqual(await finance_service.inspect(self.guild,finance),'archived threads exist')
        finance.archived=[]
        self.guild.threads=[SimpleNamespace(parent_id=finance.id)]
        self.assertEqual(await finance_service.inspect(self.guild,finance),'active threads exist')
        self.guild.threads=[]
        db.set_setting('some_active_resource',finance.id)
        self.assertIn('stored setting dependency',await finance_service.inspect(self.guild,finance))
        db.set_setting('some_active_resource','')
        with db.connect() as conn:
            conn.execute('INSERT INTO temp_voice_channels VALUES (?,?,?)',(finance.id,20,1))
        self.assertEqual(await finance_service.inspect(self.guild,finance),'stored resource dependency: temp_voice_channels.channel_id')
        with db.connect() as conn:
            conn.execute('DELETE FROM temp_voice_channels')
        finance.permissions_for=lambda member:discord.Permissions.none()
        self.assertIn('insufficient permissions',await finance_service.inspect(self.guild,finance))

    async def test_old_overview_heading_recovers_without_duplicate(self):
        await self.setup_board()
        channel=support.resource(self.guild,'support-gamerhq')
        key=support.message_key(self.guild)
        message=channel.messages[int(db.get_setting(key))]
        message.content='# 💜 Support GamerHQ\nOld default overview'
        with db.connect() as conn:
            conn.execute('DELETE FROM managed_message_content WHERE setting_key=?',(key,))
            conn.execute('DELETE FROM settings WHERE key=?',(key,))
        await support.repair_support(self.guild,[])
        self.assertEqual(db.get_setting(key),str(message.id))
        self.assertEqual(channel.sends,1)
        self.assertTrue(message.content.startswith('# 🤝 Partners & Benefits'))

    async def test_finance_custom_state_and_ticket_dependency_remain_preserved(self):
        from services import legacy_finance_service as finance_service, managed_message_service as managed
        await self.setup_board()
        finance=self.guild.add_channel('💶・finanzberatung',self.community)
        message=self.add_message(finance,'# 💶 Finanzberatung\nCustom owner text')
        db.set_setting(support.channel_key(self.guild,'finanzberatung'),finance.id)
        key=support.message_key(self.guild,'finance')
        state={'key':key,'guild_id':self.guild.id,'channel_id':finance.id,
               'message_id':message.id,'customized':True,'content_hash':managed.digest(message.content)}
        managed.store(state)
        changed=[]
        await support.repair_support(self.guild,changed)
        self.assertTrue(any('customized or changed' in line for line in changed))
        self.assertTrue(managed.load(key)['retired'])
        self.assertFalse(message.deleted)
        with db.connect() as conn:
            conn.execute("INSERT INTO support_tickets (guild_id,channel_id,creator_discord_id,subject,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                         (self.guild.id,finance.id,20,'Historical ticket','Keep history',1,1))
        self.assertEqual(await finance_service.inspect(self.guild,finance),'stored resource dependency: support_tickets.channel_id')

    async def test_finance_history_api_failure_is_manual_review(self):
        from services import legacy_finance_service as finance_service
        await self.setup_board()
        finance=self.guild.add_channel('💶・finanzberatung',self.community)
        db.set_setting(support.channel_key(self.guild,'finanzberatung'),finance.id)
        async def denied(**kwargs):
            raise discord.Forbidden(type('Response',(),{'status':403,'reason':'Forbidden'})(),'denied')
            yield
        finance.history=denied
        self.assertEqual(await finance_service.inspect(self.guild,finance),'cannot inspect all history/threads: Forbidden')
