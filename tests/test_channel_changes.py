"""Offline managed drift approvals, privacy restoration and event loop safety."""
import asyncio
import copy
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import test_onboarding as fixtures
from database import db
from services import channel_change_service as changes, channel_adoption_service as adoption
from services import support_service as support, instant_gaming_service as ig
from services.server_setup_service import repair_server, analyze_server
from services.health_service import scan
from services.server_service import ServerMessageError
from cogs.server_changes import ServerChanges, ChangeView, log_channel


def before(channel):
    result = copy.copy(channel)
    result.overwrites = {t: discord.PermissionOverwrite.from_pair(*o.pair()) for t, o in channel.overwrites.items()}
    return result


class ChangeTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    async def asyncSetUp(self):
        self.guild.owner_id = 71
        self.owner = SimpleNamespace(id=71, guild_permissions=discord.Permissions())
        self.member = SimpleNamespace(id=72, guild_permissions=discord.Permissions())
        self.guild.get_member = lambda mid: {71:self.owner, 72:self.member}.get(mid)
        changes._expected.clear(); changes._locks.clear(); changes._timers.clear(); changes._dirty.clear()
        await repair_server(self.guild, self.bot)
        changes._expected.clear()
        self.channel = support.resource(self.guild, 'support-gamerhq')
        self.notice = self.guild.add_channel('🤖・bot-log', self.staff)
        self.notice.permissions_for = lambda target: discord.Permissions.all() if target in {self.guild.me, self.guild.mod} else discord.Permissions.none()
        self.cog = ServerChanges(self.bot)
        timer = patch.object(changes, 'DEBOUNCE', 0.01)
        timer.start(); self.addCleanup(timer.stop)

    async def asyncTearDown(self):
        pending = list(changes._timers.values())
        for task in pending: task.cancel()
        if pending: await asyncio.gather(*pending, return_exceptions=True)
        changes._timers.clear()

    async def event(self, channel, **fields):
        old = before(channel)
        for field, value in fields.items(): setattr(channel, field, value)
        await changes.detect(old, channel, self.cog.notify)
        await asyncio.sleep(0.035)
        return changes.load(self.guild.id, channel.id)

    async def rename(self):
        return await self.event(self.channel, name='✨・support-renamed')

    async def deleted(self, channel=None):
        channel = channel or self.channel
        self.guild.text_channels.remove(channel)
        await changes.detect(channel, None, self.cog.notify, deleted=True)
        await asyncio.sleep(0.035)
        return changes.load(self.guild.id, channel.id)

    async def act(self, record, action, user=None):
        return await changes.act(self.guild, user or self.owner, record['resource_id'], record['revision'], action)

    async def test_managed_rename_detected_and_unmanaged_ignored(self):
        record = await self.rename()
        self.assertEqual(record['change_types'], ['name'])
        self.assertEqual(record['risk_level'], 'low')
        self.assertEqual(self.notice.sends, 1)
        manual = self.guild.add_channel('manual', self.community)
        self.assertIsNone(await self.event(manual, name='other-manual'))

    async def test_exact_bot_change_suppression_does_not_hide_manual_fields(self):
        self.channel.name = 'old-name'
        old = before(self.channel)
        await changes.edit(self.channel, name='💜・support-gamerhq', reason='test')
        await changes.detect(old, self.channel, self.cog.notify)
        self.assertIsNone(changes.load(self.guild.id, self.channel.id))
        old = before(self.channel)
        changes.expect(self.guild, self.channel.id, name='expected')
        self.channel.name = 'manual-different'
        await changes.detect(old, self.channel, self.cog.notify)
        await asyncio.sleep(0.035)
        self.assertIsNotNone(changes.load(self.guild.id, self.channel.id))

    async def test_expected_unsafe_private_permissions_are_still_repaired(self):
        channel = ig.resolve(self.guild, 'ig-purchases')
        old = before(channel)
        channel.overwrites[self.guild.default_role].view_channel = True
        changes.expect(self.guild, channel.id, permissions=changes.permissions(channel.overwrites))
        await changes.detect(old, channel, self.cog.notify)
        self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
        self.assertEqual(changes.load(self.guild.id, channel.id)['status'], 'auto_repaired')

    async def test_unmanaged_default_position_is_reviewable_without_inventing_a_revert(self):
        record = await self.event(self.channel, position=0)
        self.assertIn('position', record['change_types'])
        self.assertEqual(record['risk_level'], 'low')
        with self.assertRaises(ServerMessageError): await self.act(record, 'revert')
        await self.act(record, 'adopt')
        self.assertIn('position', adoption.stored(self.guild, 'support-gamerhq'))

    async def test_quick_updates_coalesce_and_repeated_event_is_deduplicated(self):
        old = before(self.channel)
        self.channel.name = 'first'; self.channel.position = 0
        await changes.detect(old, self.channel, self.cog.notify)
        middle = before(self.channel)
        self.channel.name = 'final'; self.channel.category = self.community
        await changes.detect(middle, self.channel, self.cog.notify)
        await asyncio.sleep(0.035)
        record = changes.load(self.guild.id, self.channel.id)
        self.assertIn('name', record['change_types']); self.assertIn('category', record['change_types'])
        self.assertIn('position', record['change_types'])
        self.assertEqual(self.notice.sends, 1)
        await changes.detect(old, self.channel, self.cog.notify)
        await asyncio.sleep(0.035)
        self.assertEqual(changes.load(self.guild.id, self.channel.id)['revision'], record['revision'])
        self.assertEqual(self.notice.sends, 1)

    async def test_owner_adopts_name_preserving_id_and_future_sync(self):
        record = await self.rename()
        cid = self.channel.id
        self.assertEqual((await self.act(record, 'adopt'))['status'], 'adopted')
        await repair_server(self.guild, self.bot)
        self.assertEqual(self.channel.name, '✨・support-renamed')
        self.assertEqual(self.channel.id, cid)

    async def test_revert_restores_desired_and_suppresses_own_event(self):
        record = await self.rename()
        old = before(self.channel)
        result = await self.act(record, 'revert')
        self.assertEqual(result['status'], 'reverted')
        self.assertEqual(self.channel.name, '💜・support-gamerhq')
        await changes.detect(old, self.channel, self.cog.notify)
        await asyncio.sleep(0.035)
        self.assertEqual(changes.load(self.guild.id, self.channel.id)['revision'], record['revision'])
        self.assertEqual(self.notice.sends, 1)

    async def test_ignore_keeps_desired_and_health_is_read_only(self):
        record = await self.rename()
        token = changes.desired_token(self.guild, 'support-gamerhq')
        self.assertEqual((await self.act(record, 'ignore'))['status'], 'ignored')
        self.assertEqual(changes.desired_token(self.guild, 'support-gamerhq'), token)
        rows_before = changes.records(self.guild.id)
        findings = await scan(self.guild, messages=False)
        self.assertEqual(changes.records(self.guild.id), rows_before)
        self.assertEqual(self.channel.name, '✨・support-renamed')
        self.assertTrue(any(f.name == 'Managed changes: ignored' for f in findings))

    async def test_unauthorized_buttons_do_not_mutate(self):
        record = await self.rename()
        view = ChangeView(record, self.cog.notify)
        interaction = SimpleNamespace(guild=self.guild, user=self.member,
            response=SimpleNamespace(send_message=AsyncMock()))
        for button in view.children:
            await button.callback(interaction)
        self.assertEqual(interaction.response.send_message.await_count, 3)
        self.assertEqual(changes.load(self.guild.id, self.channel.id)['status'], 'pending')

    async def test_deleted_channel_restores_once_with_new_id_and_existing_desired_name(self):
        self.channel.name = '✨・saved-support'
        draft = await adoption.preview(self.guild, self.owner, self.channel.id, 'name')
        await adoption.confirm(self.guild, self.owner, draft, confirmed=True)
        record = await self.deleted()
        self.assertEqual(record['change_types'], ['deleted'])
        self.assertIsNone(self.guild.get_channel(self.channel.id))
        await self.act(record, 'restore')
        replacement = support.resource(self.guild, 'support-gamerhq')
        self.assertNotEqual(replacement.id, self.channel.id)
        self.assertEqual(replacement.name, '✨・saved-support')
        self.assertEqual(len(replacement.messages), 1)
        with self.assertRaises(ServerMessageError): await self.act(record, 'restore')

    async def test_remove_deleted_channel_requires_owner_and_survives_repair(self):
        news = ig.resolve(self.guild, 'gaming-news')
        record = await self.deleted(news)
        with self.assertRaises(ServerMessageError): await self.act(record, 'remove', self.member)
        self.assertFalse(changes.removed(self.guild, 'gaming-news'))
        await self.act(record, 'remove')
        self.assertTrue(changes.removed(self.guild, 'gaming-news'))
        await repair_server(self.guild, self.bot)
        await ig.sync(self.guild)
        self.assertIsNone(ig.resolve(self.guild, 'gaming-news'))
        self.assertIsNone(db.get_setting(ig.message_key(self.guild, 'gaming-news')))
        self.assertFalse(any(c.name == ig.CHANNELS['gaming-news'][0] for c in self.guild.text_channels))
        self.assertFalse(any(row['spec'].name == ig.CHANNELS['gaming-news'][0] for group in analyze_server(self.guild)['categories'] for row in group['channels']))

    async def test_staff_exposure_immediately_repaired_without_unsafe_button_or_loop(self):
        channel = ig.resolve(self.guild, 'ig-purchases')
        old = before(channel)
        channel.overwrites[self.guild.default_role].view_channel = True
        exposed = before(channel)
        await changes.detect(old, channel, self.cog.notify)
        self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
        record = changes.load(self.guild.id, channel.id)
        self.assertEqual(record['status'], 'auto_repaired')
        self.assertIsNone(next(iter(self.notice.messages.values())).view)
        await changes.detect(exposed, channel, self.cog.notify)
        await asyncio.sleep(0.035)
        self.assertEqual(changes.load(self.guild.id, channel.id)['revision'], record['revision'])

    async def test_unrelated_role_exposure_and_removed_bot_access_are_high_risk(self):
        channel = ig.resolve(self.guild, 'ig-buyer-ranking')
        old = before(channel)
        channel.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True)
        await changes.detect(old, channel, self.cog.notify)
        self.assertFalse(channel.overwrites_for(self.guild.custom).view_channel)
        old = before(channel)
        channel.overwrites[self.guild.me].send_messages = False
        await changes.detect(old, channel, self.cog.notify)
        self.assertTrue(channel.overwrites_for(self.guild.me).send_messages)
        self.assertEqual(changes.load(self.guild.id, channel.id)['risk_level'], 'high')

    async def test_position_is_low_risk_and_permission_adoption_is_narrow(self):
        amazon = support.resource(self.guild, 'amazon')
        record = await self.event(amazon, position=999)
        self.assertEqual(record['risk_level'], 'low')
        self.assertEqual(record['status'], 'pending')
        await self.act(record, 'adopt')
        old = before(amazon)
        amazon.overwrites[self.guild.default_role].send_messages = True
        await changes.detect(old, amazon, self.cog.notify)
        await asyncio.sleep(0.035)
        record = changes.load(self.guild.id, amazon.id)
        self.assertEqual(record['risk_level'], 'medium')
        await self.act(record, 'adopt')
        await repair_server(self.guild, self.bot)
        self.assertTrue(amazon.overwrites_for(self.guild.default_role).send_messages)

    async def test_unknown_permission_grants_cannot_be_imported(self):
        old = before(self.channel)
        self.channel.overwrites[self.guild.custom] = discord.PermissionOverwrite(send_messages=True)
        await changes.detect(old, self.channel, self.cog.notify)
        await asyncio.sleep(0.035)
        record = changes.load(self.guild.id, self.channel.id)
        with self.assertRaises(ServerMessageError): await self.act(record, 'adopt')
        self.assertNotIn('send_messages', adoption.stored(self.guild, 'support-gamerhq'))

    async def test_stale_buttons_expiry_and_private_log_fail_closed(self):
        record = await self.rename()
        self.channel.name = 'another-change'
        with self.assertRaises(ServerMessageError): await self.act(record, 'adopt')
        record['detected_at'] = time.time() - changes.PENDING_TTL - 1
        changes.store(record); changes.expire()
        self.assertEqual(changes.load(self.guild.id, self.channel.id)['status'], 'expired')
        self.notice.permissions_for = lambda target: discord.Permissions.all()
        self.assertIsNone(log_channel(self.guild))
        self.assertIsNone(await self.event(self.guild.add_channel('unmanaged', self.start), name='renamed'))

    async def test_expired_expected_changes_do_not_suppress_manual_edits(self):
        changes.expect(self.guild, self.channel.id, name='manual')
        changes._expected[(self.guild.id, self.channel.id, 'name')] = [('manual', time.monotonic() - 1)]
        record = await self.event(self.channel, name='manual')
        self.assertEqual(record['status'], 'pending')

    async def test_high_risk_failed_repair_is_recorded_not_approved(self):
        channel = ig.resolve(self.guild, 'ig-purchases')
        old = before(channel)
        channel.overwrites[self.guild.default_role].view_channel = True
        error = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        with patch.object(channel, 'edit', AsyncMock(side_effect=error)):
            await changes.detect(old, channel, self.cog.notify)
        record = changes.load(self.guild.id, channel.id)
        self.assertEqual(record['status'], 'repair_failed')
        with self.assertRaises(ServerMessageError): await self.act(record, 'adopt')

    async def test_persisted_view_and_record_survive_cog_recreation(self):
        record = await self.rename()
        changes._expected.clear()
        restarted = ServerChanges(self.bot)
        view = ChangeView(changes.load(self.guild.id, self.channel.id), restarted.notify)
        self.assertTrue(view.is_persistent())
        await restarted.notify(self.guild, record)
        self.assertEqual(self.notice.sends, 1)
        interaction = SimpleNamespace(guild=self.guild, user=self.owner,
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        await view.children[2].callback(interaction)
        self.assertEqual(changes.load(self.guild.id, self.channel.id)['status'], 'ignored')
        self.assertIsNone(next(iter(self.notice.messages.values())).view)

    async def test_ambiguous_restore_attempt_does_not_create_duplicates(self):
        record = await self.deleted()
        error = discord.HTTPException(SimpleNamespace(status=500, reason='Server Error'), 'uncertain')
        with patch.object(self.start, 'create_text_channel', AsyncMock(side_effect=error)) as create:
            with self.assertRaises(discord.HTTPException): await self.act(record, 'restore')
            with self.assertRaises(ServerMessageError): await self.act(record, 'restore')
            self.assertEqual(create.await_count, 1)

    async def test_missing_private_log_retains_pending_for_later_delivery(self):
        self.notice.permissions_for = lambda target: discord.Permissions.all()
        record = await self.rename()
        self.assertIsNone(record['message_id'])
        self.assertEqual(self.notice.sends, 0)
        self.notice.permissions_for = lambda target: discord.Permissions.all() if target in {self.guild.me, self.guild.mod} else discord.Permissions.none()
        await self.cog.notify(self.guild, record)
        self.assertEqual(self.notice.sends, 1)
