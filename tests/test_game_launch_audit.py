"""Offline proof for the operator-private projection, not live inventory evidence."""
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

import test_game_visibility as fixtures
from database import db, tenants
from services import game_launch_audit as audit


class LaunchAuditTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the established Games DB/Discord fixtures.
    setUp = fixtures.VisibilityTests.setUp
    make_game = fixtures.VisibilityTests.make_game
    create = fixtures.VisibilityTests.create
    set_visible = fixtures.VisibilityTests.set_visible

    async def asyncSetUp(self):
        await fixtures.VisibilityTests.asyncSetUp(self)
        for role in self.guild.roles:
            role.guild = self.guild
        for category in self.guild.categories:
            category.permissions_for = lambda member: discord.Permissions.all()

    def binding(self, guild_id=None):
        return tenants._bind(tenants.GuildTenant(guild_id or self.guild.id, Path(db.DB_PATH)))

    async def report(self):
        with self.binding():
            return await audit.collect(self.guild, self.actor, environment='staging',
                                       guild_id=self.guild.id, application_id=self.guild.me.id)

    def snapshot(self):
        with db.connect() as conn:
            return '\n'.join(conn.iterdump())

    async def test_channel_inventory_failure_has_narrow_safe_phase(self):
        before = self.snapshot()
        self.guild.fetch_channels.side_effect = RuntimeError('private Discord payload')
        diagnostic = {}
        with self.assertRaisesRegex(RuntimeError, 'private Discord payload'):
            with self.binding():
                await audit.collect(self.guild, self.actor, environment='staging',
                                    guild_id=self.guild.id, application_id=self.guild.me.id,
                                    diagnostic=diagnostic)
        self.assertEqual(diagnostic['stage'], 'GAMES_CHANNELS_FETCH')
        self.assertEqual(diagnostic['channelFetchFailureClass'], 'OTHER')
        self.assertNotIn('private Discord payload', json.dumps(diagnostic))
        self.guild.fetch_roles.assert_not_awaited()
        self.assertEqual(before, self.snapshot())

    async def test_channel_inventory_model_failure_is_classified_without_leaking(self):
        before = self.snapshot()
        self.guild.fetch_channels.side_effect = KeyError('private channel payload')
        diagnostic = {}
        with self.assertRaises(KeyError):
            with self.binding():
                await audit.collect(self.guild, self.actor, environment='staging',
                                    guild_id=self.guild.id, application_id=self.guild.me.id,
                                    diagnostic=diagnostic)
        self.assertEqual(diagnostic['stage'], 'GAMES_CHANNELS_FETCH')
        self.assertEqual(diagnostic['channelFetchFailureClass'], 'MODEL_KEY')
        self.assertNotIn('private channel payload', json.dumps(diagnostic))
        self.guild.fetch_roles.assert_not_awaited()
        self.assertEqual(before, self.snapshot())

    async def test_verified_rest_inventory_reuses_complete_models_without_refetch(self):
        before = self.snapshot()
        channels = self.guild.fetch_channels.return_value
        roles = self.guild.fetch_roles.return_value
        diagnostic = {}
        with self.binding():
            result = await audit.collect(
                self.guild, self.actor, environment='staging',
                guild_id=self.guild.id, application_id=self.guild.me.id,
                diagnostic=diagnostic, verified_rest_inventory=(channels, roles))
        self.assertEqual(diagnostic['stage'], 'GAMES_FINAL_GUARDS')
        self.assertEqual(result['inventory'], {'state': 'VERIFIED'})
        self.guild.fetch_channels.assert_not_awaited()
        self.guild.fetch_roles.assert_not_awaited()
        self.assertEqual(before, self.snapshot())

    async def test_roles_inventory_failure_has_narrow_safe_phase(self):
        before = self.snapshot()
        self.guild.fetch_roles.side_effect = RuntimeError('private role payload')
        diagnostic = {}
        with self.assertRaisesRegex(RuntimeError, 'private role payload'):
            with self.binding():
                await audit.collect(self.guild, self.actor, environment='staging',
                                    guild_id=self.guild.id, application_id=self.guild.me.id,
                                    diagnostic=diagnostic)
        self.assertEqual(diagnostic['stage'], 'GAMES_ROLES_FETCH')
        self.guild.fetch_channels.assert_awaited_once()
        self.assertEqual(before, self.snapshot())

    async def test_snapshot_construction_failure_has_narrow_safe_phase(self):
        before = self.snapshot()
        diagnostic = {}
        with patch.object(audit.visibility, 'GuildSnapshot',
                          side_effect=RuntimeError('private channel model')):
            with self.assertRaises(RuntimeError):
                with self.binding():
                    await audit.collect(self.guild, self.actor, environment='staging',
                                        guild_id=self.guild.id, application_id=self.guild.me.id,
                                        diagnostic=diagnostic)
        self.assertEqual(diagnostic['stage'], 'GAMES_SNAPSHOT_BUILD')
        self.guild.fetch_channels.assert_awaited_once()
        self.guild.fetch_roles.assert_awaited_once()
        self.assertEqual(before, self.snapshot())

    async def test_full_catalog_includes_inactive_entries_without_writes(self):
        with db.connect() as conn:
            conn.execute('UPDATE games SET active=0,selectable=0 WHERE id=?', (self.game['id'],))
        before = self.snapshot()
        result = await self.report()
        row = next(row for row in result['games'] if row['catalogId'] == self.game['id'])
        self.assertFalse(row['active'])
        self.assertEqual(row['status'], 'NOT_SET_UP')
        self.assertFalse(result['channelIntentVerified'])
        self.assertEqual(before, self.snapshot())
        self.guild.create_category.assert_not_awaited()
        self.guild.create_text_channel.assert_not_awaited()
        self.guild.create_role.assert_not_awaited()
        self.guild.fetch_channels.assert_awaited_once()
        self.guild.fetch_roles.assert_awaited_once()

    async def test_denied_inventory_is_unknown_not_missing_and_does_not_leak_exception(self):
        self.guild.fetch_channels.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason='Forbidden'), 'synthetic-private-response')
        result = await self.report()
        self.assertTrue(all(row['status'] == 'UNVERIFIED' for row in result['games']))
        self.assertTrue(all(row['roleVerified'] is None for row in result['games']))
        self.assertNotIn('synthetic-private-response', json.dumps(result))

    async def test_unbound_or_wrong_tenant_cannot_read_any_inventory(self):
        for context in (None, self.binding(self.guild.id + 1)):
            if context:
                context.__enter__()
            try:
                with self.assertRaises(PermissionError):
                    await audit.collect(self.guild, self.actor, environment='staging',
                                        guild_id=self.guild.id, application_id=self.guild.me.id)
            finally:
                if context:
                    context.__exit__(None, None, None)
        self.guild.fetch_channels.assert_not_awaited()

    async def test_readiness_pending_history_and_repeat_are_preserved(self):
        room = await self.set_visible(True)
        await self.set_visible(False)
        before = self.snapshot()
        first, second = await self.report(), await self.report()
        self.assertEqual(first, second)
        self.assertEqual(before, self.snapshot())
        row = next(row for row in first['games'] if row['catalogId'] == self.game['id'])
        self.assertEqual((row['status'], row['canonicalChannelId']), ('HIDDEN', room.id))
        db.set_setting(f'game_visibility_operation:{self.guild.id}:{self.game["id"]}',
                       json.dumps({'state': 'PENDING', 'visible': False}))
        pending_before = self.snapshot()
        result = await self.report()
        row = next(row for row in result['games'] if row['catalogId'] == self.game['id'])
        self.assertEqual((row['status'], row['operationPending']), ('REVIEW', True))
        self.assertEqual(pending_before, self.snapshot())

    async def test_unexpected_write_is_denied_by_sqlite_not_silently_allowed(self):
        before = self.snapshot()
        with patch.object(audit.readiness, 'inspect_one', side_effect=lambda *args: db.set_setting('unexpected', 'write')):
            with self.assertRaises(sqlite3.DatabaseError):
                await self.report()
        self.assertEqual(before, self.snapshot())

    async def test_logical_identity_collision_is_review_not_cross_environment_match(self):
        await self.make_game('Terraria!')
        for role in self.guild.roles:
            role.guild = self.guild
        result = await self.report()
        rows = [row for row in result['games'] if row['logicalKey'] == 'terraria']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['status'] == 'REVIEW' and row['reason'] == 'LOGICAL_GAME_IDENTITY_CONFLICT'
                            for row in rows))

    async def test_stale_mapping_remains_review_without_creating_replacement(self):
        with db.connect() as conn:
            conn.execute('UPDATE games SET channel_id=999999 WHERE id=?', (self.game['id'],))
        before = self.snapshot()
        result = await self.report()
        row = next(row for row in result['games'] if row['catalogId'] == self.game['id'])
        self.assertEqual(row['status'], 'REVIEW')
        self.assertEqual(row['canonicalChannelId'], 999999)
        self.assertFalse(row['canonicalChannelVerified'])
        self.assertEqual(before, self.snapshot())
        self.guild.create_text_channel.assert_not_awaited()

    async def test_foreign_inventory_and_revoked_actor_fail_closed(self):
        self.guild.roles[0].guild = SimpleNamespace(id=self.guild.id + 1)
        with self.assertRaisesRegex(ValueError, 'FOREIGN_INVENTORY_REJECTED'):
            await self.report()
        self.guild.roles[0].guild = self.guild
        async def revoke():
            self.guild.get_member = lambda _: None
            return self.guild.roles
        self.guild.fetch_roles.side_effect = revoke
        with self.assertRaisesRegex(PermissionError, 'GAMES_AUDIT_BINDING_REJECTED'):
            await self.report()
