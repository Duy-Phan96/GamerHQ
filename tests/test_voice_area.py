"""Voice owner isolation and multi-area confirmation regressions."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import test_music_cleanup as fixtures
import test_onboarding as onboarding_fixtures
from database import db
from services import temp_voice_service as voice, area_management_service as areas, game_area_cleanup as cleanup
from services import game_service
from cogs.area import AreaMenu, AreaSelect, AreaConfirm
from cogs.voice_controls import VoicePanel
from services.community_structure_service import core_channel, guide_text
from services.server_setup_service import repair_server


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.MusicCleanupTests.setUp
    role = fixtures.MusicCleanupTests.role
    channel = fixtures.MusicCleanupTests.channel
    link_game = fixtures.MusicCleanupTests.link_game

    async def asyncSetUp(self):
        self.actor.guild = self.guild; self.actor.roles = [self.everyone]; self.actor.bot = False
        self.room = self.channel(200, 'Room', discord.VoiceChannel, self.category)
        self.room.overwrites[self.actor] = discord.PermissionOverwrite(view_channel=True, connect=True)
        self.room.overwrites[self.music] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, use_voice_activation=True)
        db.add_temp_voice(200, self.actor.id, self.game['id'])
        async def edit(**kwargs):
            for key, value in kwargs.items():
                if key != 'reason': setattr(self.room,key,value)
            return self.room
        self.room.edit = AsyncMock(side_effect=edit)
        self.other = MagicMock(spec=discord.Member); self.other.id=21; self.other.guild=self.guild
        self.other.roles=[self.everyone]; self.other.guild_permissions=discord.Permissions(); self.other.bot=False
        self.other.voice=SimpleNamespace(channel=self.room); self.other.move_to=AsyncMock()
        self.guild.get_member.side_effect=lambda uid: self.other if uid==21 else self.actor if uid==10 else None
        voice._locks.clear()

    async def test_owner_rename_limit_and_scope(self):
        await voice.act(self.guild,self.actor,200,'rename',' New room ')
        self.assertEqual(self.room.name,'New room')
        await voice.act(self.guild,self.actor,200,'limit','6')
        self.assertEqual(self.room.user_limit,6)
        for value in ('-1','100','x'):
            with self.assertRaises(ValueError): await voice.act(self.guild,self.actor,200,'limit',value)
        with self.assertRaises(ValueError): await voice.act(self.guild,self.other,200,'rename','stolen')
        with self.assertRaises(ValueError): await voice.act(self.guild,self.actor,self.generator.id,'rename','permanent')
        db.add_temp_voice(200,21,self.game['id'])
        self.actor.id=22; self.actor.roles=[self.everyone]
        with self.assertRaises(ValueError): await voice.act(self.guild,self.actor,200,'close')
        self.generator.delete.assert_not_awaited()

    async def test_lock_unlock_music_privacy_and_restart_state(self):
        original=self.room.overwrites_for(self.everyone).view_channel
        await voice.act(self.guild,self.actor,200,'lock')
        self.assertFalse(self.room.overwrites_for(self.everyone).connect)
        self.assertFalse(self.room.overwrites_for(self.game_role).connect)
        self.assertTrue(self.room.overwrites_for(self.music).connect)
        self.assertFalse(self.room.overwrites_for(self.music).administrator)
        self.assertEqual(self.room.overwrites_for(self.everyone).view_channel,original)
        self.assertTrue(db.get_setting('temp_voice_lock:200'))
        voice._locks.clear()
        await voice.act(self.guild,self.actor,200,'unlock')
        self.assertIsNone(self.room.overwrites_for(self.everyone).connect)
        self.assertIsNone(self.room.overwrites_for(self.game_role).connect)
        self.assertFalse(db.get_setting('temp_voice_lock:200'))
        self.category.set_permissions.assert_not_awaited()

    async def test_allow_remove_does_not_kick_ban_or_moderate_another_room(self):
        await voice.act(self.guild,self.actor,200,'invite',21)
        self.assertTrue(self.room.overwrites_for(self.other).connect)
        await voice.act(self.guild,self.actor,200,'remove',21)
        self.other.move_to.assert_awaited_once_with(None,reason=unittest.mock.ANY)
        self.assertFalse(self.room.overwrites_for(self.other).connect)
        self.other.kick.assert_not_called(); self.other.ban.assert_not_called()
        self.other.voice.channel=self.generator
        with self.assertRaises(ValueError): await voice.act(self.guild,self.actor,200,'remove',21)
        self.assertEqual(self.other.move_to.await_count,1)

    async def test_staff_controls_and_nonstaff_panel_denied(self):
        mod=self.role(5,'Mod'); mod.permissions=discord.Permissions(manage_messages=True)
        self.other.roles.append(mod)
        await voice.act(self.guild,self.other,200,'rename','Staff rename')
        self.assertEqual(self.room.name,'Staff rename')
        self.other.roles.remove(mod)
        interaction=SimpleNamespace(guild=self.guild,user=self.other,response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await VoicePanel(200).interaction_check(interaction))

    async def test_owner_leaves_occupied_room_retained_then_empty_deletes(self):
        self.room.members=[self.other]
        await voice.empty_cleanup(self.room)
        self.room.delete.assert_not_awaited()
        self.assertEqual(db.get_temp_voice(200)['host_id'],10)
        self.room.members=[]
        db.set_setting('temp_voice_lock:200','{}')
        await voice.empty_cleanup(self.room)
        self.assertIsNone(db.get_temp_voice(200))
        self.assertFalse(db.get_setting('temp_voice_lock:200'))

    async def test_failed_empty_delete_keeps_tracking_and_close_cleans(self):
        self.room.delete.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'test')
        with self.assertRaises(discord.Forbidden): await voice.empty_cleanup(self.room)
        self.assertIsNotNone(db.get_temp_voice(200))
        self.room.delete.side_effect=None
        await voice.act(self.guild,self.actor,200,'close')
        self.assertIsNone(db.get_temp_voice(200))

    async def test_legacy_owner_permissions_removed_without_touching_others(self):
        self.room.overwrites[self.actor]=discord.PermissionOverwrite(manage_channels=True,move_members=True,view_channel=True)
        await voice.restrict_legacy_owner(self.room)
        self.assertFalse(self.room.overwrites_for(self.actor).manage_channels)
        self.assertFalse(self.room.overwrites_for(self.actor).move_members)
        self.assertTrue(self.room.overwrites_for(self.actor).view_channel)


class AreaTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.MusicCleanupTests.setUp
    role = fixtures.MusicCleanupTests.role
    channel = fixtures.MusicCleanupTests.channel
    link_game = fixtures.MusicCleanupTests.link_game
    event = fixtures.MusicCleanupTests.event

    def visible(self):
        with db.connect() as conn: conn.execute('UPDATE games SET selectable=1,active=1 WHERE id=?',(self.game['id'],))

    async def test_manual_visible_area_removed_game_role_and_selection_retained(self):
        self.visible()
        rows=areas.preview(self.guild,self.actor,{self.game['id']})
        self.assertTrue(rows[0]['safe'])
        self.assertFalse(cleanup.inspect_area(self.guild,db.get_game_by_id(self.game['id']))['safe'])
        self.category.delete.assert_not_awaited()
        result=await areas.remove_many(self.guild,self.actor,rows)
        self.assertIn('Removed area',result[0])
        game=db.get_game_by_id(self.game['id'])
        self.assertEqual(game['selectable'],1); self.assertEqual(game['role_id'],self.game_role.id)
        self.assertIsNone(game['category_id']); self.game_role.delete.assert_not_called()

    async def test_event_and_temp_dependencies_and_recheck(self):
        rows=areas.preview(self.guild,self.actor,{self.game['id']})
        self.event()
        result=await areas.remove_many(self.guild,self.actor,rows)
        self.assertIn('Skipped',result[0]); self.category.delete.assert_not_awaited()
        self.assertFalse(areas.preview(self.guild,self.actor,{self.game['id']})[0]['safe'])
        with db.connect() as conn: conn.execute('DELETE FROM lfg_events')
        db.add_temp_voice(300,10,self.game['id'])
        self.assertFalse(areas.preview(self.guild,self.actor,{self.game['id']})[0]['safe'])

    async def test_auth_and_protected_core_category(self):
        self.actor.id=21
        with self.assertRaises(ValueError): areas.preview(self.guild,self.actor,{self.game['id']})
        with self.assertRaises(ValueError): await areas.create_many(self.guild,self.actor,{self.game['id']})
        self.actor.guild_permissions=discord.Permissions(administrator=True)
        self.category.name='EVENTS'
        self.assertFalse(areas.preview(self.guild,self.actor,{self.game['id']})[0]['safe'])

    async def test_large_catalog_paginated_selection_and_preview_does_not_delete(self):
        for n in range(55):
            game=db.upsert_custom_game(f'Game {n}','🎮','Test')
            with db.connect() as conn: conn.execute('UPDATE games SET selectable=1 WHERE id=?',(game['id'],))
        view=AreaSelect(self.guild,10,'add')
        self.assertEqual(len(view.games),55)
        select=next(c for c in view.children if isinstance(c,discord.ui.Select))
        self.assertEqual(len(select.options),20)
        interaction=SimpleNamespace(user=self.actor,response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock()))
        select._values=[select.options[0].value,select.options[1].value]
        await select.callback(interaction)
        await view.next_page.callback(interaction)
        self.assertEqual(len(view.selected),2)
        await AreaMenu(self.guild,10).remove.callback(interaction)
        self.assertIsInstance(interaction.response.edit_message.call_args.kwargs['view'],AreaSelect)
        remove=AreaSelect(self.guild,10,'remove'); remove.selected={self.game['id']}
        await remove.proceed.callback(interaction)
        confirm=interaction.response.edit_message.call_args.kwargs['view']
        self.assertIsInstance(confirm,AreaConfirm)
        self.category.delete.assert_not_awaited()
        await confirm.cancel.callback(interaction)
        self.category.delete.assert_not_awaited()

    async def test_multi_add_existing_skipped_and_fresh_factory_used(self):
        self.visible()
        second=db.upsert_custom_game('Second','🎮','Test')
        with db.connect() as conn: conn.execute('UPDATE games SET selectable=1 WHERE id=?',(second['id'],))
        async def create(guild,game,plan,*,fresh):
            self.assertTrue(fresh)
            with db.connect() as conn: conn.execute('UPDATE games SET category_id=888,area_enabled=1 WHERE id=?',(game['id'],))
        with patch.object(areas,'create_game_structure_confirmed',AsyncMock(side_effect=create)) as factory:
            result=await areas.create_many(self.guild,self.actor,{self.game['id'],second['id']})
            self.assertIn('Already existed',result[0]); self.assertIn('Created',result[1])
            await areas.create_many(self.guild,self.actor,{second['id']})
            self.assertEqual(factory.await_count,1)

    async def test_fresh_creation_rechecks_after_lock(self):
        self.visible()
        with self.assertRaises(game_service.GameStructureError):
            await game_service.create_game_structure_confirmed(self.guild,self.game,{},fresh=True)
        self.guild.create_category.assert_not_awaited()

    async def test_bulk_factory_creates_template_reuses_role_and_applies_music(self):
        self.guild.channels.clear(); self.guild.categories.clear()
        db.deactivate_game(self.game['id']); self.visible()
        self.game_role.name = 'Renamed existing game role'
        async def category(**kwargs):
            ch=self.channel(300,kwargs['name'],discord.CategoryChannel)
            ch.overwrites=kwargs['overwrites']; return ch
        async def text(name,**kwargs): return self.channel(301,name,discord.TextChannel,kwargs['category'])
        async def generator(name,**kwargs): return self.channel(302,name,discord.VoiceChannel,kwargs['category'])
        self.guild.create_category=AsyncMock(side_effect=category)
        self.guild.create_text_channel=AsyncMock(side_effect=text)
        self.guild.create_voice_channel=AsyncMock(side_effect=generator)
        result=await areas.create_many(self.guild,self.actor,{self.game['id']})
        self.assertIn('Created',result[0])
        current=db.get_game_by_id(self.game['id'])
        self.assertEqual(current['role_id'],self.game_role.id)
        self.assertEqual(current['category_id'],300)
        self.assertTrue(self.guild.get_channel(302).overwrites_for(self.music).speak)
        self.guild.create_role.assert_not_awaited()
        await areas.create_many(self.guild,self.actor,{self.game['id']})
        self.assertEqual(self.guild.create_category.await_count,1)

    async def test_removal_confirmation_is_one_use_and_cancel_never_deletes(self):
        rows=areas.preview(self.guild,self.actor,{self.game['id']})
        view=AreaConfirm(self.guild,10,rows,AreaSelect(self.guild,10,'remove'))
        interaction=SimpleNamespace(user=self.actor,response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),edit_original_response=AsyncMock())
        await view.confirm.callback(interaction)
        await view.confirm.callback(interaction)
        self.category.delete.assert_awaited_once()


class GuideTests(unittest.IsolatedAsyncioTestCase):
    setUp=onboarding_fixtures.OnboardingTests.setUp
    add_message=onboarding_fixtures.OnboardingTests.add_message

    async def test_plain_guide_reused_renamed_and_pin_updated(self):
        guide=self.guild.add_channel('guide',self.start)
        old=self.add_message(guide,'# 📘 GamerHQ Guide\nOld text',pinned=True)
        user=self.add_message(guide,'User pin',author=20,pinned=True)
        await repair_server(self.guild,self.bot)
        await repair_server(self.guild,self.bot)
        self.assertEqual(guide.name,'📘・guide')
        self.assertIs(core_channel(self.guild,'guide'),guide)
        self.assertEqual(guide.sends,0)
        self.assertIn('Coming Soon',old.content)
        self.assertIn('/voice manage',old.content)
        self.assertNotIn('lifecycle',old.content)
        self.assertFalse(user.deleted)
        self.assertFalse(guide.overwrites[self.guild.default_role].send_messages)
        self.assertLess(len(guide_text(self.guild)),2000)


if __name__=='__main__': unittest.main()
