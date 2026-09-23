"""Offline adoption authorization, persistence, stale previews and reconciliation."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

import discord
import test_onboarding as fixtures
from database import db
from services import channel_adoption_service as adopt, support_service as support
from services.server_setup_service import repair_server, analyze_server
from services.server_service import ServerMessageError
from services.health_service import scan
from cogs.server import AdoptChannelView


class AdoptionTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    async def asyncSetUp(self):
        adopt._locks.clear()
        self.guild.owner_id = 71
        self.admin = SimpleNamespace(id=70, guild_permissions=discord.Permissions(administrator=True))
        self.owner = SimpleNamespace(id=71, guild_permissions=discord.Permissions())
        self.member = SimpleNamespace(id=72, guild_permissions=discord.Permissions())
        self.members = {m.id: m for m in (self.admin, self.owner, self.member)}
        self.guild.get_member = self.members.get
        await repair_server(self.guild, self.bot)
        self.channel = support.resource(self.guild, 'support-gamerhq')

    def settings(self):
        with db.connect() as conn:
            return list(map(tuple, conn.execute('SELECT * FROM settings ORDER BY key')))

    def interaction(self, user=None):
        return SimpleNamespace(user=user or self.admin, guild=self.guild,
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
            edit_original_response=AsyncMock())

    async def test_purple_rename_keeps_id_messages_history_and_permissions(self):
        channel = self.channel
        channel.name = '💙・support-gamerhq'
        note = self.add_message(channel, 'Unrelated history', author=72, pinned=True)
        ids = [c.id for c in self.guild.channels]
        pin = db.get_setting(support.message_key(self.guild))
        rights = copy.deepcopy(channel.overwrites)
        await support.sync_support_messages(self.guild, order=True)
        await repair_server(self.guild, self.bot)
        self.assertEqual(channel.name, '💜・support-gamerhq')
        self.assertEqual([c.id for c in self.guild.channels], ids)
        self.assertEqual(db.get_setting(support.message_key(self.guild)), pin)
        self.assertEqual(channel.overwrites, rights)
        self.assertFalse(note.deleted)
        self.assertEqual(note.edits, 0)
        self.assertEqual(channel.sends, 1)

    async def test_preview_confirm_name_repeat_sync_and_repair(self):
        self.channel.name = '✨・community-support'
        before = self.settings()
        draft = await adopt.preview(self.guild, self.owner, self.channel.id, 'name')
        self.assertEqual(self.settings(), before)
        self.assertEqual(draft['desired']['name'], '💜・support-gamerhq')
        self.assertEqual(draft['actual']['name'], self.channel.name)
        self.assertIn('community-support', adopt.render(self.guild, draft))
        self.assertTrue(await adopt.confirm(self.guild, self.owner, draft, confirmed=True))
        self.channel.name = 'unintended-rename'
        await support.sync_support_messages(self.guild, order=True)
        await repair_server(self.guild, self.bot)
        self.assertEqual(self.channel.name, '✨・community-support')
        draft = await adopt.preview(self.guild, self.owner, self.channel.id, 'name')
        before = self.settings()
        self.assertFalse(await adopt.confirm(self.guild, self.owner, draft, confirmed=True))
        self.assertEqual(self.settings(), before)
        self.assertEqual(self.channel.sends, 1)
        self.assertTrue(any(row['channel'] == self.channel for cat in analyze_server(self.guild)['categories'] for row in cat['channels']))

    async def test_unmanaged_unsupported_and_unauthorized_rejected(self):
        for user, channel in ((self.member, self.channel),
                              (self.admin, self.guild.add_channel('manual', self.community)),
                              (self.admin, self.guild.get_channel(int(db.get_setting('managed_channel:1:ig-purchases'))))):
            with self.subTest(channel=channel.name, user=user.id):
                before = self.settings()
                with self.assertRaises(ServerMessageError):
                    await adopt.preview(self.guild, user, channel.id)
                self.assertEqual(before, self.settings())

    async def test_cancel_and_unconfirmed_adoption_do_not_write(self):
        self.channel.name = 'custom-name'
        draft = await adopt.preview(self.guild, self.admin, self.channel.id, 'name')
        before = self.settings()
        with self.assertRaises(ServerMessageError):
            await adopt.confirm(self.guild, self.admin, draft)
        view = AdoptChannelView(self.guild, draft)
        await view.cancel_adoption.callback(self.interaction())
        await view.confirm_adoption.callback(self.interaction())
        self.assertEqual(before, self.settings())

    async def test_stale_discord_preview_and_revoked_admin_rejected(self):
        draft = await adopt.preview(self.guild, self.admin, self.channel.id, 'name')
        before = self.settings()
        self.channel.name = 'changed-after-preview'
        with self.assertRaises(ServerMessageError):
            await adopt.confirm(self.guild, self.admin, draft, confirmed=True)
        draft = await adopt.preview(self.guild, self.admin, self.channel.id, 'name')
        self.admin.guild_permissions = discord.Permissions()
        with self.assertRaises(ServerMessageError):
            await adopt.confirm(self.guild, self.admin, draft, confirmed=True)
        self.assertEqual(before, self.settings())

    async def test_selective_adoption_does_not_import_other_changes_or_permissions(self):
        self.channel.name = 'custom-name'
        self.channel.category = self.community
        self.channel.overwrites[self.guild.custom] = discord.PermissionOverwrite(send_messages=True)
        before = copy.deepcopy(self.channel.overwrites)
        draft = await adopt.preview(self.guild, self.admin, self.channel.id, 'name')
        await adopt.confirm(self.guild, self.admin, draft, confirmed=True)
        self.assertEqual(set(adopt.stored(self.guild, 'support-gamerhq')), {'name', 'channel_id'})
        self.assertEqual(self.channel.overwrites, before)
        self.assertEqual(adopt.desired(self.guild, 'support-gamerhq')['category'], self.start.id)
        with self.assertRaises(ServerMessageError):
            await adopt.preview(self.guild, self.admin, self.channel.id, 'permissions')

    async def test_category_and_order_survive_setup_sync_and_health(self):
        from services.instant_gaming_service import sync
        news = support.resource(self.guild, 'gaming-news')
        news.category = self.community
        news.position = 0
        news.name = '🗞️・news-feed'
        draft = await adopt.preview(self.guild, self.admin, news.id)
        await adopt.confirm(self.guild, self.admin, draft, confirmed=True)
        await repair_server(self.guild, self.bot)
        await sync(self.guild)
        await support.sync_support_messages(self.guild, order=True)
        self.assertEqual(news.category_id, self.community.id)
        self.assertEqual(news.name, '🗞️・news-feed')
        self.assertEqual(adopt.children(self.guild.channels, self.community.id)[draft['actual']['position']].id, news.id)
        before = self.settings()
        rows = await scan(self.guild, messages=False)
        self.assertEqual(before, self.settings())
        self.assertFalse(any(f.name in {'gaming-news', 'Adopted gaming-news', 'Partner channel order'} and f.state != 'PASS' for f in rows))
        news.name = 'drift'
        rows = await scan(self.guild, messages=False)
        self.assertEqual(next(f.state for f in rows if f.name == 'Adopted gaming-news'), 'REPAIRABLE')
        self.assertEqual(news.name, 'drift')

    async def test_adopted_partner_position_overrides_default_order(self):
        amazon = support.resource(self.guild, 'amazon')
        amazon.position = -1  # Fixture: place before all siblings as a manual move.
        draft = await adopt.preview(self.guild, self.admin, amazon.id, 'position')
        await adopt.confirm(self.guild, self.admin, draft, confirmed=True)
        await repair_server(self.guild, self.bot)
        await support.sync_support_messages(self.guild, order=True)
        self.assertEqual(adopt.children(self.guild.channels, amazon.category_id)[0].id, amazon.id)
        self.assertEqual(adopt.stored(self.guild, 'amazon')['position'], 0)

    async def test_private_category_and_name_collision_rejected(self):
        self.channel.category = self.staff
        with self.assertRaises(ServerMessageError):
            await adopt.preview(self.guild, self.admin, self.channel.id, 'category')
        self.channel.name = 'gaming-news'
        with self.assertRaises(ServerMessageError):
            await adopt.preview(self.guild, self.admin, self.channel.id, 'name')

    async def test_multiple_adopted_positions_keep_free_games_following_deals(self):
        news = support.resource(self.guild, 'gaming-news')
        deals = support.resource(self.guild, 'gaming-deals')
        news.position, deals.position = 100, 101
        for channel in (news, deals):
            draft = await adopt.preview(self.guild, self.admin, channel.id, 'position')
            await adopt.confirm(self.guild, self.admin, draft, confirmed=True)
        await adopt.apply_order(self.guild)
        before = len(self.guild.position_updates)
        await adopt.apply_order(self.guild)
        ordered = adopt.children(self.guild.channels, news.category_id)
        free = support.resource(self.guild, 'free-games')
        self.assertEqual([c.id for c in ordered[-3:]], [news.id, deals.id, free.id])
        self.assertEqual(before, len(self.guild.position_updates))

    async def test_confirmation_ui_binds_actor_and_rejects_stale_persisted_state(self):
        self.channel.name = '✨・help-gamerhq'
        draft = await adopt.preview(self.guild, self.admin, self.channel.id, 'name')
        view = AdoptChannelView(self.guild, draft)
        await view.confirm_adoption.callback(self.interaction(self.owner))
        self.assertEqual(adopt.stored(self.guild, 'support-gamerhq'), {})
        await view.confirm_adoption.callback(self.interaction())
        self.assertEqual(adopt.stored(self.guild, 'support-gamerhq')['name'], self.channel.name)
        with self.assertRaises(ServerMessageError):
            await adopt.confirm(self.guild, self.admin, draft, confirmed=True)
