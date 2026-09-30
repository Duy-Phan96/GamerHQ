"""Owner log ACL size, effective access, private history and setup retry regressions."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs import owner_changelog as log
from database import db
from services import structure_adoption_service as structure


class OwnerLogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = patch.object(db, 'DB_PATH', Path(self.temp.name) / 'owner-log.db')
        store.start()
        self.addCleanup(store.stop)
        db.init_db()
        log._locks.clear()
        log._setup_locks.clear()
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id, self.guild.owner_id = 1, 71
        self.everyone = self.role(1)
        self.staff = self.role(20, manage_messages=True)
        self.administrator = self.role(21, administrator=True)
        self.guild.default_role = self.everyone
        self.guild.roles = [self.everyone, self.staff, self.administrator]
        self.guild.get_role.side_effect = lambda rid: next((r for r in self.guild.roles if r.id == rid), None)
        self.owner = self.member(71)
        self.bot = self.member(900)
        self.bot.bot = True
        self.normal = self.member(80, self.staff)
        self.admin = self.member(81, self.administrator)
        self.guild.me = self.bot
        self.guild.get_member.side_effect = lambda uid: {71: self.owner, 900: self.bot, 80: self.normal, 81: self.admin}.get(uid)
        self.parent = MagicMock(spec=discord.CategoryChannel)
        self.parent.id, self.parent.name, self.parent.guild = 77, '🔒 STAFF', self.guild
        self.parent.overwrites = {self.everyone: discord.PermissionOverwrite(view_channel=False), self.staff: discord.PermissionOverwrite(view_channel=True)}
        self.parent.overwrites_for.side_effect = lambda target: self.parent.overwrites.get(target, discord.PermissionOverwrite())
        self.guild.categories, self.guild.channels, self.guild.text_channels = [self.parent], [self.parent], []
        self.guild.get_channel.side_effect = lambda cid: next((c for c in self.guild.channels if c.id == cid), None)

    def role(self, rid, **bits):
        role = MagicMock(spec=discord.Role)
        role.id = rid
        role.permissions = discord.Permissions(view_channel=True, read_message_history=True, send_messages=True, **bits)
        role._permissions = role.permissions.value  # discord.py uses the raw bitfield for member roles.
        return role

    def member(self, uid, role=None):
        member = MagicMock(spec=discord.Member)
        member.id, member.guild, member.bot = uid, self.guild, False
        member._roles = discord.utils.SnowflakeList([role.id] if role else [])
        member.guild_permissions = role.permissions if role else discord.Permissions.none()
        member.is_timed_out.return_value = False
        return member

    def request(self, member=None):
        return SimpleNamespace(guild=self.guild, user=member or self.owner,
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
            edit_original_response=AsyncMock())

    def channel(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.guild, channel.name = 456, self.guild, log.LABEL
        channel.category_id, channel.category = self.parent.id, self.parent
        channel.overwrites = log.owner_rights(self.guild)
        channel.overwrites_for.side_effect = lambda target: channel.overwrites.get(target, discord.PermissionOverwrite())
        channel.permissions_for.return_value = discord.Permissions.all()
        return channel

    def test_hundreds_of_roles_still_use_only_three_overwrites(self):
        self.guild.roles.extend(self.role(1000 + n) for n in range(250))
        original_roles = list(self.guild.roles)
        rights = log.owner_rights(self.guild)
        self.assertEqual(set(rights), {self.everyone, self.owner, self.bot})
        self.assertEqual(len(rights), 3)
        self.assertFalse(rights[self.everyone].view_channel)
        self.assertTrue(rights[self.owner].view_channel)
        self.assertTrue(rights[self.bot].send_messages)
        self.assertEqual(self.guild.roles, original_roles)
        for role in self.guild.roles:
            role.edit.assert_not_called()

    def test_repair_prunes_obsolete_grants_without_mutating_original(self):
        old = {self.role(1000 + n): discord.PermissionOverwrite(view_channel=True) for n in range(150)}
        old[self.normal] = discord.PermissionOverwrite(view_channel=True)
        resource = SimpleNamespace(overwrites=old)
        rights = log.owner_rights(self.guild, resource)
        self.assertEqual(set(rights), {self.everyone, self.owner, self.bot})
        self.assertEqual(len(old), 151)
        self.assertTrue(all(value.view_channel is True for value in old.values()))
        self.assertNotIn(self.staff, rights)
        self.assertNotIn(self.normal, rights)

    def test_effective_staff_access_is_denied_but_administrator_bypasses_acl(self):
        rights = log.owner_rights(self.guild)
        data = {'id': '456', 'name': 'owner-changelog', 'type': 0, 'position': 0, 'parent_id': '77',
                'permission_overwrites': [
                    {'id': str(target.id), 'type': 0 if isinstance(target, discord.Role) else 1,
                     'allow': str(value.pair()[0].value), 'deny': str(value.pair()[1].value)}
                    for target, value in rights.items()]}
        channel = discord.TextChannel(state=MagicMock(), guild=self.guild, data=data)
        self.assertFalse(channel.permissions_for(self.normal).view_channel)
        self.assertTrue(channel.permissions_for(self.owner).view_channel)
        self.assertTrue(channel.permissions_for(self.bot).view_channel)
        self.assertTrue(channel.permissions_for(self.admin).view_channel)
        self.assertTrue(self.parent.overwrites[self.staff].view_channel)

    def test_missing_owner_cache_does_not_enumerate_roles_and_missing_bot_is_rejected(self):
        self.guild.get_member.return_value = None
        self.guild.get_member.side_effect = None
        self.assertEqual(len(log.owner_rights(self.guild)), 2)
        self.guild.me = None
        with self.assertRaises(ValueError):
            log.owner_rights(self.guild)

    def test_channel_launcher_does_not_query_or_publish_history(self):
        with patch.object(structure, 'recent_changes', side_effect=AssertionError('history leaked to channel')):
            text = log.render(self.guild)
        self.assertIn('Review / Undo', text)
        self.assertIn('Administrators', text)
        self.assertNotIn('<@', text)

    async def test_nonowner_admin_cannot_open_history_or_undo(self):
        request = self.request(self.admin)
        with patch.object(structure, 'recent_changes') as history:
            await log.Entry().open.callback(request)
        history.assert_not_called()
        self.assertTrue(request.response.send_message.call_args.kwargs['ephemeral'])
        view = log.Undo(1, 71, {'id': 99, 'action': 'channel_update', 'reversible': True, 'status': 'APPLIED'})
        with patch.object(structure, 'undo_change', AsyncMock()) as undo:
            await view.children[0].callback(request)
        undo.assert_not_awaited()
        request.response.defer.assert_not_awaited()

    async def test_owner_history_is_ephemeral(self):
        request = self.request()
        with patch.object(structure, 'recent_changes', return_value=[]) as history:
            await log.Entry().open.callback(request)
        history.assert_called_once_with(self.guild, 25)
        self.assertTrue(request.response.send_message.call_args.kwargs['ephemeral'])
        self.assertIsInstance(request.response.send_message.call_args.kwargs['view'], log.ChangeList)

    def test_exposed_channel_blocks_background_publication(self):
        channel = self.channel()
        self.guild.channels.append(channel)
        db.set_setting(log.key(self.guild), channel.id)
        self.assertIs(log.destination(self.guild), channel)
        channel.overwrites[self.normal] = discord.PermissionOverwrite(view_channel=True)
        self.assertIsNone(log.destination(self.guild))
        channel.overwrites.pop(self.normal)
        channel.overwrites[self.staff] = discord.PermissionOverwrite(view_channel=True)
        self.assertIsNone(log.destination(self.guild))

    async def test_setup_publishes_from_returned_channel_despite_gateway_lag_and_reuses_id(self):
        self.guild.roles.extend(self.role(1000 + n) for n in range(150))
        channel = self.channel()
        async def create(*args, **kwargs):
            self.assertLessEqual(len(kwargs['overwrites']), 3)
            channel.overwrites = kwargs['overwrites']
            return channel
        self.guild.create_text_channel = AsyncMock(side_effect=create)
        async def publish(target, **kwargs):
            self.assertIs(target, channel)
            self.assertTrue(kwargs['pin'])
            self.assertTrue(kwargs['view'].is_persistent())
            self.assertIn('launcher', kwargs['content'])
            db.set_setting(kwargs['setting_key'], 789)
            return SimpleNamespace(id=789)
        with patch('services.server_service.upsert_fixed_message', AsyncMock(side_effect=publish)) as upsert:
            request = self.request()
            await log.open_management(request)
            self.guild.create_text_channel.assert_not_called()
            view = request.response.send_message.call_args.kwargs['view']
            await view.confirm.callback(request)
            await view.confirm.callback(request)
            self.guild.create_text_channel.assert_awaited_once()
            self.assertEqual(db.get_setting(log.key(self.guild)), '456')
            self.assertEqual(db.get_setting(log.board_key(self.guild)), '789')
            self.assertIn('ready', request.edit_original_response.call_args.kwargs['content'])
            self.assertIsNone(self.guild.get_channel(456))
            self.guild.channels.append(channel)
            self.guild.text_channels.append(channel)
            channel.edit = AsyncMock(return_value=channel)
            again = self.request()
            await log.open_management(again)
            await again.response.send_message.call_args.kwargs['view'].confirm.callback(again)
            self.guild.create_text_channel.assert_awaited_once()
            channel.edit.assert_awaited_once()
            self.assertEqual(len(channel.edit.call_args.kwargs['overwrites']), 3)
            self.assertFalse(channel.edit.call_args.kwargs['sync_permissions'])
            self.assertEqual(upsert.await_count, 2)

    async def test_definite_400_rejection_allows_a_fresh_setup_retry(self):
        self.guild.create_text_channel = AsyncMock(side_effect=discord.HTTPException(
            SimpleNamespace(status=400, reason='Bad Request'), 'Invalid Form Body'))
        for _ in range(2):
            request = self.request()
            await log.open_management(request)
            await request.response.send_message.call_args.kwargs['view'].confirm.callback(request)
            self.assertIsNone(db.get_setting(log.key(self.guild)))
            self.assertIsNone(db.get_setting(f'owner_changelog_creation:{self.guild.id}'))
        self.assertEqual(self.guild.create_text_channel.await_count, 2)

    async def test_uncertain_creation_does_not_create_another_channel(self):
        self.guild.create_text_channel = AsyncMock(side_effect=discord.HTTPException(
            SimpleNamespace(status=502, reason='Bad Gateway'), 'uncertain'))
        for _ in range(2):
            request = self.request()
            await log.open_management(request)
            await request.response.send_message.call_args.kwargs['view'].confirm.callback(request)
        self.guild.create_text_channel.assert_awaited_once()
        self.assertEqual(db.get_setting(f'owner_changelog_creation:{self.guild.id}'), 'reserved')

    async def test_stale_stored_id_is_not_replaced(self):
        db.set_setting(log.key(self.guild), 456)
        request = self.request()
        await log.open_management(request)
        self.guild.create_text_channel.assert_not_called()
        self.assertNotIn('view', request.response.send_message.call_args.kwargs)

    async def test_same_name_unmapped_channel_is_not_adopted(self):
        self.guild.text_channels.append(self.channel())
        request = self.request()
        await log.open_management(request)
        await request.response.send_message.call_args.kwargs['view'].confirm.callback(request)
        self.guild.create_text_channel.assert_not_called()
        self.assertIsNone(db.get_setting(log.key(self.guild)))
