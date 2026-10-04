"""Admin visibility uses one non-destructive Games channel lifecycle, offline."""
import asyncio
import copy
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import test_game_channels_v2 as fixtures
from database import db
from services import game_channel_service as channels
from services import game_visibility_service as visibility
from services import server_operations


class DetachedChannel(fixtures.TextChannel):
    @property
    def category_id(self):
        return self.category.id if self.category else None


class VisibilityTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ChannelTests.setUp
    make_game = fixtures.ChannelTests.make_game
    create = fixtures.ChannelTests.create

    async def asyncSetUp(self):
        await fixtures.ChannelTests.asyncSetUp(self)
        self.kind = patch.object(fixtures.fixtures, 'FakeChannel', DetachedChannel)
        self.kind.start()
        self.addCleanup(self.kind.stop)
        self.guild.fetch_roles = AsyncMock(side_effect=lambda: list(self.guild.roles))
        self.guild.fetch_channels = AsyncMock(side_effect=lambda: list(self.guild.channels))
        self.guild.me.guild_permissions = discord.Permissions.all()
        self.guild.create_category = AsyncMock(wraps=self.guild.create_category)
        self.guild.create_role = AsyncMock(wraps=self.guild.create_role)
        self.initial_messages = sum(len(c.messages) for c in self.guild.text_channels)

    def remove_shared_category(self):
        self.guild.categories.remove(self.gaming)
        with db.connect() as conn:
            conn.execute("DELETE FROM settings WHERE key IN ('managed_category:1:gaming','managed_category:1:games')")

    async def set_visible(self, enabled, game=None, **kwargs):
        plan = await visibility.preview(self.guild, self.actor, (game or self.game)['id'], enabled)
        return await visibility.apply(self.guild, self.actor, plan, **kwargs)

    async def legacy(self, *, detached=False):
        old = self.guild.add_category('OLD GAME')
        chat = self.guild.add_channel('chat', old)
        chat.messages[4000] = SimpleNamespace(content='preserved history')
        voice = self.guild.add_channel('lfg-do-not-touch', old)
        with db.connect() as conn:
            conn.execute('UPDATE games SET category_id=?,chat_channel_id=?,lfg_channel_id=?,area_enabled=1 WHERE id=?',
                         (old.id, chat.id, voice.id, self.game['id']))
        if detached:
            self.guild.categories.remove(old)
            chat.category = None
            voice.category = None
        return old, chat, voice

    async def test_missing_category_and_three_real_games_without_member_threshold(self):
        self.remove_shared_category()
        games = [self.game, await self.make_game('Ark'), await self.make_game('Valheim')]
        with patch('config.GAME_CHANNEL_MEMBER_THRESHOLD', 1000):
            for game in games:
                channel = await self.set_visible(True, game)
                self.assertEqual(db.get_game_by_id(game['id'])['channel_id'], channel.id)
        self.guild.create_category.assert_awaited_once()
        self.assertEqual(self.guild.create_text_channel.await_count, 3)
        parent = channels.category(self.guild)
        self.assertEqual(parent.name, '🎮 Games')
        self.assertEqual(len(parent.channels), 3)
        self.assertEqual([c.name for c in sorted(parent.channels,key=lambda c:c.position)], ['ark','terraria','valheim'])
        for game in games:
            await self.set_visible(True, game)
        self.assertEqual(self.guild.create_text_channel.await_count, 3)

    async def test_preview_and_cancel_do_not_create_anything_or_change_db(self):
        self.remove_shared_category()
        before = db.get_game_by_id(self.game['id'])
        plan = await visibility.preview(self.guild,self.actor,self.game['id'],True)
        self.assertEqual(plan['category_action'],'create')
        self.assertIn('Create the shared',visibility.description(plan))
        self.assertEqual(before,db.get_game_by_id(self.game['id']))
        self.guild.create_category.assert_not_awaited()
        self.guild.create_text_channel.assert_not_awaited()

    async def test_hide_and_reshow_preserve_identity_history_and_roles(self):
        room = await self.set_visible(True)
        room.messages[9999] = SimpleNamespace(content='keep')
        roles = list(self.guild.roles)
        role = self.guild.get_role(self.game['role_id'])
        hidden = await self.set_visible(False)
        self.assertEqual(hidden.id,room.id)
        self.assertFalse(hidden.overwrites_for(role).view_channel)
        self.assertFalse(hidden.overwrites_for(self.guild.default_role).view_channel)
        self.assertTrue(hidden.overwrites_for(self.guild.mod).view_channel)
        self.assertFalse(db.get_game_by_id(self.game['id'])['selectable'])
        self.assertEqual(room.messages[9999].content,'keep')
        self.assertEqual(self.guild.roles,roles)
        # Repair must retain the explicit hide policy, not reopen the room.
        definition=next(r for r in server_operations.definitions(self.guild) if r.get('game_id')==self.game['id'])
        self.assertEqual(server_operations.rights(self.guild,definition,room),room.overwrites)
        channels._guild_locks.clear()  # Restart: state is in the database.
        shown = await self.set_visible(True)
        self.assertEqual(shown.id,room.id)
        self.assertTrue(shown.overwrites_for(role).view_channel)
        self.assertEqual(shown.messages[9999].content,'keep')
        self.guild.create_text_channel.assert_awaited_once()

    async def test_hide_without_channel_creates_nothing(self):
        self.remove_shared_category()
        result=await self.set_visible(False)
        self.assertIsNone(result)
        self.guild.create_category.assert_not_awaited()
        self.guild.create_text_channel.assert_not_awaited()
        self.assertFalse(db.get_game_by_id(self.game['id'])['selectable'])

    async def test_detached_legacy_chat_moves_with_same_id_and_other_rooms_survive(self):
        old,chat,other=await self.legacy(detached=True)
        result=await self.set_visible(True)
        self.assertEqual(result.id,chat.id)
        self.assertEqual(chat.messages[4000].content,'preserved history')
        self.assertEqual(chat.category_id,self.gaming.id)
        self.assertIsNone(other.category_id)
        self.assertIn(other,self.guild.text_channels)
        self.assertEqual(channels.legacy_hints(db.get_game_by_id(self.game['id']))['lfg_channel_id'],other.id)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_canonical_channel_at_wrong_location_is_moved_not_skipped(self):
        old,chat,other=await self.legacy()
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=? WHERE id=?',(chat.id,self.game['id']))
        await self.set_visible(True)
        self.assertEqual(chat.category_id,self.gaming.id)
        self.assertEqual(chat.name,'terraria')
        self.assertIn(old,self.guild.categories)
        self.assertEqual(other.category_id,old.id)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_unique_unmapped_games_category_is_only_linked_after_confirmation(self):
        with db.connect() as conn:
            conn.execute("DELETE FROM settings WHERE key='managed_category:1:gaming'")
        plan=await visibility.preview(self.guild,self.actor,self.game['id'],True)
        self.assertEqual(plan['category_action'],'link')
        self.assertIsNone(db.get_setting('managed_category:1:games'))
        await visibility.apply(self.guild,self.actor,plan)
        self.assertEqual(db.get_setting('managed_category:1:games'),str(self.gaming.id))
        self.guild.create_category.assert_not_awaited()

    async def test_ambiguous_and_stale_category_mappings_block_without_duplicates(self):
        db.set_setting('managed_category:1:games',111111)
        with self.assertRaises(ValueError): await self.set_visible(True)
        self.guild.create_category.assert_not_awaited()
        self.guild.create_text_channel.assert_not_awaited()

    async def test_duplicate_clicks_and_concurrent_shows_create_once(self):
        plan=await visibility.preview(self.guild,self.actor,self.game['id'],True)
        results=await asyncio.gather(visibility.apply(self.guild,self.actor,plan),
                                    visibility.apply(self.guild,self.actor,plan),return_exceptions=True)
        self.assertTrue(any(not isinstance(x,Exception) for x in results))
        self.guild.create_text_channel.assert_awaited_once()

    async def test_stale_preview_refuses_changed_channel_without_overwriting(self):
        room=await self.set_visible(True)
        plan=await visibility.preview(self.guild,self.actor,self.game['id'],False)
        room.name='newer-name'
        with self.assertRaisesRegex(ValueError,'changed'): await visibility.apply(self.guild,self.actor,plan)
        self.assertTrue(db.get_game_by_id(self.game['id'])['selectable'])
        self.assertEqual(room.name,'newer-name')

    async def test_current_admin_revocation_and_expired_preview(self):
        plan=await visibility.preview(self.guild,self.actor,self.game['id'],True)
        plan['created']=time.time()-300
        with self.assertRaises(ValueError): await visibility.apply(self.guild,self.actor,plan)
        plan['created']=time.time()
        self.guild.get_member=lambda _:None
        with self.assertRaises(ValueError): await visibility.apply(self.guild,self.actor,plan)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_bot_permissions_checked_before_creating_category(self):
        self.remove_shared_category()
        self.guild.me.guild_permissions=discord.Permissions.none()
        with self.assertRaisesRegex(ValueError,'Manage Channels'): await self.set_visible(True)
        self.guild.create_category.assert_not_awaited()

    async def test_missing_role_and_foreign_channel_are_blocked(self):
        with db.connect() as conn:
            conn.execute('UPDATE games SET role_id=555555 WHERE id=?',(self.game['id'],))
        with self.assertRaisesRegex(ValueError,'role is missing'): await self.set_visible(True)
        db.set_game_role(self.game['id'],self.game['role_id'])
        foreign=self.guild.add_channel('terraria',self.gaming)
        with self.assertRaisesRegex(ValueError,'same-named'): await self.set_visible(True)
        self.assertIn(foreign,self.guild.text_channels)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_missing_role_mapping_creates_safe_role_and_channel(self):
        game=db.upsert_custom_game('New Role Game','🎮','Other')
        result=await self.set_visible(True,game)
        saved=db.get_game_by_id(game['id'])
        role=self.guild.get_role(saved['role_id'])
        self.assertEqual(role.permissions.value,0)
        self.assertTrue(result.overwrites_for(role).view_channel)
        self.assertTrue(saved['selectable'])

    async def test_member_overwrite_is_not_destroyed_and_unexpected_allow_blocks(self):
        room=await self.set_visible(True)
        room.overwrites[self.guild.custom]=discord.PermissionOverwrite(attach_files=False,view_channel=False)
        await self.set_visible(False)
        self.assertFalse(room.overwrites[self.guild.custom].attach_files)
        await self.set_visible(True)
        self.assertFalse(room.overwrites[self.guild.custom].attach_files)
        room.overwrites[self.guild.custom].view_channel=True
        with self.assertRaisesRegex(ValueError,'additional access'): await self.set_visible(False)
        self.assertTrue(room.overwrites[self.guild.custom].view_channel)

    async def test_partial_hide_failure_is_pending_and_repair_cannot_reopen(self):
        room=await self.set_visible(True)
        original=room.edit
        room.edit=AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'denied'))
        with self.assertRaises(discord.Forbidden): await self.set_visible(False)
        status=json.loads(db.get_setting(visibility.operation_key(self.guild,self.game['id'])))
        self.assertEqual(status['state'],'PENDING')
        role=self.guild.get_role(self.game['role_id'])
        self.assertFalse(channels.overwrites(self.guild,role,room)[role].view_channel)
        room.edit=original
        await self.set_visible(False)
        self.assertFalse(room.overwrites_for(role).view_channel)

    async def test_unknown_creation_result_never_duplicates_after_restart(self):
        self.guild.create_text_channel.side_effect=discord.HTTPException(SimpleNamespace(status=502,reason='Bad Gateway'),'unknown')
        with self.assertRaises(discord.HTTPException): await self.set_visible(True)
        channels._guild_locks.clear()
        with self.assertRaisesRegex(ValueError,'previous channel creation'): await self.set_visible(True)
        self.guild.create_text_channel.assert_awaited_once()

    async def test_definite_rejection_can_be_retried(self):
        create=self.guild.create_text_channel.side_effect
        self.guild.create_text_channel.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'denied')
        with self.assertRaises(discord.Forbidden): await self.set_visible(True)
        self.guild.create_text_channel.side_effect=create
        room=await self.set_visible(True)
        self.assertIsNotNone(room)
        self.assertEqual(self.guild.create_text_channel.await_count,2)

    async def test_intentional_deletion_is_not_recreated(self):
        db.set_setting(f'game_channel_removed:1:{self.game["id"]}', '1')
        with self.assertRaisesRegex(ValueError,'deliberately removed'): await self.set_visible(True)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_fresh_rest_result_not_cache_controls_confirmation(self):
        room=await self.set_visible(True)
        plan=await visibility.preview(self.guild,self.actor,self.game['id'],False)
        fresh=copy.copy(room)
        fresh.name='changed-remotely'
        self.guild.fetch_channels.side_effect=lambda:[fresh if c.id==room.id else c for c in self.guild.channels]
        with self.assertRaisesRegex(ValueError,'changed'): await visibility.apply(self.guild,self.actor,plan)
        self.assertTrue(db.get_game_by_id(self.game['id'])['selectable'])

    async def test_three_candidate_setup_skips_hidden_inactive_and_already_correct(self):
        await self.set_visible(True)
        other=await self.make_game('Hidden')
        db.set_game_selectable(other['id'],False)
        inactive=await self.make_game('Inactive')
        with db.connect() as conn:
            conn.execute('UPDATE games SET active=0 WHERE id=?',(inactive['id'],))
        for name in ('A','B','C','D'): await self.make_game(name)
        summary=await visibility.sync_preview(self.guild,self.actor)
        self.assertEqual(summary['ready'],1)
        self.assertEqual([p['game']['name'] for p in summary['plans']],['A','B','C'])
        self.guild.create_text_channel.assert_awaited_once()

    async def test_sort_reuses_game_slots_and_never_resets_global_order(self):
        room=await self.set_visible(True)
        room.position=80
        unrelated=self.guild.add_channel('unrelated',self.staff)
        unrelated.position=5
        second=await self.make_game('Ark')
        original=self.guild.create_text_channel.side_effect
        async def create(*args,**kwargs):
            result=await original(*args,**kwargs)
            result.position=100
            return result
        self.guild.create_text_channel.side_effect=create
        await self.set_visible(True,second)
        payload=self.guild.position_updates[-1]
        self.assertEqual({p['position'] for p in payload},{80,100})
        self.assertNotIn(unrelated.id,{p['id'] for p in payload})
        self.assertEqual(unrelated.position,5)

    async def test_capacity_and_soft_limit_are_not_silently_bypassed(self):
        with patch.object(visibility,'CATEGORY_LIMIT',0):
            with self.assertRaisesRegex(ValueError,'50 channels'): await self.set_visible(True)
        with patch('config.GAME_CHANNEL_SOFT_LIMIT',0):
            plan=await visibility.preview(self.guild,self.actor,self.game['id'],True)
            self.assertTrue(plan['override'])
            with self.assertRaisesRegex(ValueError,'soft limit'): await visibility.apply(self.guild,self.actor,plan)
            await visibility.apply(self.guild,self.actor,plan,override=True)

    async def test_slash_and_library_use_same_review_without_direct_mutation(self):
        from cogs.games import Games
        from cogs.game_channels import LibraryGame
        request=SimpleNamespace(guild=self.guild,user=self.actor,
            response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),edit_original_response=AsyncMock())
        with patch('cogs.game_channels.open_visibility',AsyncMock()) as open_review:
            await Games.set_visible.callback(SimpleNamespace(),request,self.game['name'],True)
            open_review.assert_awaited_once_with(request,self.game['id'],True)
            view=LibraryGame(self.guild,self.actor.id,self.game['id'])
            with patch.object(view,'interaction_check',AsyncMock(return_value=True)):
                await view.children[1].callback(request)
            self.assertEqual(open_review.call_args.args,(request,self.game['id'],False))
        self.guild.create_text_channel.assert_not_awaited()


    async def test_category_and_chat_returned_before_cache_updates_are_persisted(self):
        self.remove_shared_category()
        rest_only = []
        async def create_category(name, **kwargs):
            parent = fixtures.fixtures.FakeCategory(self.guild, name, 70001)
            parent.overwrites = kwargs['overwrites']
            rest_only.append(parent)
            return parent
        async def create_channel(name, **kwargs):
            room = DetachedChannel(self.guild, name, kwargs['category'], 70002)
            room.overwrites = kwargs['overwrites']
            rest_only.append(room)
            return room
        self.guild.create_category.side_effect = create_category
        self.guild.create_text_channel.side_effect = create_channel
        self.guild.fetch_channels.side_effect = lambda: list(self.guild.channels) + rest_only
        self.guild.fetch_channel.side_effect = lambda cid: next((r for r in self.guild.channels + rest_only if r.id == cid), None)
        room = await self.set_visible(True)
        self.assertEqual(room.id, 70002)
        self.assertIsNone(self.guild.get_channel(room.id))
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'], 70002)
        self.assertEqual(db.get_setting('managed_category:1:games'), '70001')
        again = await self.set_visible(True)
        self.assertEqual(again.id, room.id)
        self.guild.create_category.assert_awaited_once()
        self.guild.create_text_channel.assert_awaited_once()

    async def test_failed_verification_is_pending_not_success(self):
        room = await self.set_visible(True)
        old = copy.copy(room)
        old.overwrites = dict(room.overwrites)
        self.guild.fetch_channel.return_value = old
        self.guild.fetch_channel.side_effect = None
        with self.assertRaisesRegex(ValueError, 'did not verify'):
            await self.set_visible(False)
        state = json.loads(db.get_setting(visibility.operation_key(self.guild, self.game['id'])))
        self.assertEqual(state['state'], 'PENDING')

    async def test_repair_still_removes_unexpected_positive_access(self):
        room = await self.set_visible(True)
        room.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True, attach_files=False)
        role = self.guild.get_role(self.game['role_id'])
        desired = channels.overwrites(self.guild, role, room)
        self.assertFalse(desired[self.guild.custom].view_channel)
        self.assertFalse(desired[self.guild.custom].attach_files)
        self.assertTrue(room.overwrites[self.guild.custom].view_channel)

    async def test_cancelled_and_repeated_confirmations_are_safe(self):
        from cogs.game_channels import VisibilityConfirmation
        plan = await visibility.preview(self.guild, self.actor, self.game['id'], True)
        request = SimpleNamespace(guild=self.guild, user=self.actor,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
            edit_original_response=AsyncMock())
        view = VisibilityConfirmation(self.guild, self.actor.id, plan)
        with patch.object(view, 'interaction_check', AsyncMock(return_value=True)):
            await view.cancel(request)
            await view.confirm(request)
        self.guild.create_text_channel.assert_not_awaited()
        view = VisibilityConfirmation(self.guild, self.actor.id, plan)
        with patch.object(view, 'interaction_check', AsyncMock(return_value=True)):
            await view.confirm(request)
            await view.confirm(request)
        self.guild.create_text_channel.assert_awaited_once()

    async def test_role_hierarchy_blocks_before_category_creation(self):
        self.remove_shared_category()
        role = self.guild.get_role(self.game['role_id'])
        role.__lt__.return_value = False
        with self.assertRaisesRegex(ValueError, 'above the bot'):
            await self.set_visible(True)
        self.guild.create_category.assert_not_awaited()


    async def test_sync_does_not_silently_drop_visible_games_with_missing_roles(self):
        game = db.upsert_custom_game('No mapping yet', '🎮', 'Other')
        db.set_game_selectable(game['id'], True)
        broken = await self.make_game('Broken role')
        with db.connect() as conn:
            conn.execute('UPDATE games SET role_id=987654321 WHERE id=?', (broken['id'],))
        summary = await visibility.sync_preview(self.guild, self.actor)
        self.assertTrue(any(p['game']['id'] == game['id'] and p['role_action'] == 'create' for p in summary['plans']))
        self.assertTrue(any('Broken role' in item and 'role is missing' in item for item in summary['blocked']))
