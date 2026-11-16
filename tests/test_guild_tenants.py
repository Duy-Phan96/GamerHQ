"""Two-guild rehearsals over the production Host APIs and disposable SQLite."""
import asyncio
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import discord
import pytest

from database import db, tenants
from hosts.gamerhq.event_cutover import owns_events
from hosts.gamerhq.skill_packages import load_external_skill_packages
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from services import discord_permissions, server_operations, server_templates
from skill_runtime.contracts import OnceSchedule
from skill_runtime.runtime.scheduler import ScheduledJob
from tests.test_onboarding import FakeGuild
from tests.test_skill_discord_host import FakeSkill


class GuildTenantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        workspace = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(workspace)
        self.primary = self.root / 'legacy.sqlite'
        self.tenant_dir = self.root / 'tenants'
        self.enterContext(patch.object(db, 'DB_PATH', self.primary))
        db.init_db()
        db.set_setting('legacy_obs', 'preserved')
        self.store = self.registry(2, 3)

    def registry(self, *guilds):
        return tenants.GuildTenantStore(primary_guild=1, primary_database=self.primary,
                                       tenant_directory=self.tenant_dir, additional_guilds=guilds)

    @staticmethod
    def guild(guild_id):
        owner = SimpleNamespace(id=42, guild_permissions=discord.Permissions(administrator=True))
        guild = FakeGuild()
        guild.id, guild.owner_id = guild_id, owner.id
        guild.default_role.id = guild_id
        guild.roles = [guild.default_role]
        guild.members = [owner]
        guild.me.guild_permissions = discord.Permissions(**dict.fromkeys(discord_permissions.CORE_INSTALL, True))
        guild.sequence = 1000 * guild_id
        return guild, owner

    def test_admission_resume_and_wrong_database_fail_closed(self):
        legacy_bytes = self.primary.read_bytes()
        self.assertFalse(self.store.provision(1))
        self.assertFalse(self.tenant_dir.exists())
        with self.assertRaises(PermissionError):
            self.store.provision(37)
        with self.assertRaises(PermissionError):
            with self.store.scope(2):
                self.fail('Missing databases cannot be used')
        self.assertTrue(self.store.provision(2))
        self.assertFalse(self.store.provision(2))
        with self.store.scope(2):
            self.assertIsNone(db.get_setting('legacy_obs'))
            db.set_setting('retained', 'second guild')
            with self.assertRaises(PermissionError):
                with self.store.scope(1):
                    self.fail('Nested guild changes must fail')
        with self.assertRaises(PermissionError):
            with self.registry().scope(2):
                self.fail('Removed guild cannot open its retained database')
        with self.registry(2).scope(2):
            self.assertEqual(db.get_setting('retained'), 'second guild')
        with db.read_only():
            self.assertEqual(db.get_setting('legacy_obs'), 'preserved')
            with self.assertRaises(PermissionError):
                with self.store.scope(2):
                    self.fail('An existing snapshot cannot change guild')
        wrong = self.tenant_dir / '3.sqlite'
        shutil.copyfile(self.tenant_dir / '2.sqlite', wrong)
        wrong.chmod(0o600)
        with self.assertRaisesRegex(PermissionError, 'ownership'):
            self.store.provision(3)
        self.assertEqual(db.current_path(), self.primary)
        self.assertEqual(self.primary.read_bytes(), legacy_bytes)

    async def test_concurrent_template_and_skill_only_operations_use_disjoint_stores(self):
        self.store.provision(2)
        first, owner = self.guild(1)
        second, _ = self.guild(2)
        guilds = {1: first, 2: second}
        runtime = GamerHQSkillRuntime(SimpleNamespace(get_guild=guilds.get))
        skill = FakeSkill()
        runtime.register(skill)
        skill_id = skill.manifest.id
        await runtime.scheduler_store.upsert_job(
            ScheduledJob(88, skill_id, 'legacy', 'test', OnceSchedule(100), {}, 100))
        ready = asyncio.Event()

        async def setup_core():
            with self.store.scope(1):
                db.set_setting('shared_legacy_key', 'primary')
                ready.set()
                draft = await server_templates.preview(guilds[1], owner, server_templates.GAMERHQ)
                self.assertTrue(draft['structure']['rows'])
                self.assertFalse(await runtime.state.installed_skill_ids(guild_id=1))
                with self.assertRaises(PermissionError):
                    await server_templates.preview(guilds[2], owner, server_templates.GAMERHQ)
                await runtime.scheduler_store.upsert_job(
                    ScheduledJob(1, skill_id, 'same', 'test', OnceSchedule(100), {'guild': 1}, 100))

        async def skill_only():
            await ready.wait()
            with self.store.scope(2):
                self.assertIsNone(db.get_setting('shared_legacy_key'))
                db.set_setting('shared_legacy_key', 'secondary')
                self.assertTrue(await runtime.install_skill(guild_id=2, skill_id=skill_id))
                self.assertTrue(await runtime.enable_skill(guild_id=2, skill_id=skill_id))
                self.assertFalse(await runtime.enable_skill(guild_id=2, skill_id=skill_id))
                await runtime.scheduler_store.upsert_job(
                    ScheduledJob(2, skill_id, 'same', 'test', OnceSchedule(100), {'guild': 2}, 100))
                with self.assertRaises(PermissionError):
                    await runtime.install_skill(guild_id=1, skill_id=skill_id)
                self.assertIsNone(server_templates.progress(guilds[2], server_templates.GAMERHQ))

        await asyncio.gather(setup_core(), skill_only())
        self.assertIsNone(tenants.current())
        restored = GamerHQSkillRuntime(SimpleNamespace(get_guild=guilds.get))
        restored.register(FakeSkill())
        for guild_id, label in ((1, 'primary'), (2, 'secondary')):
            with self.store.scope(guild_id):
                with db.read_only():
                    self.assertEqual(db.get_setting('shared_legacy_key'), label)
                self.assertEqual(await restored.restore_guild(guild_id=guild_id),
                                 (skill_id,) if guild_id == 2 else ())
                jobs = await restored.scheduler_store.list_jobs(guild_id=guild_id)
                self.assertEqual([(job.key, job.payload['guild']) for job in jobs], [('same', guild_id)])
                claimed = await restored.scheduler_store.claim_due(now=100, lease_seconds=30, limit=10)
                self.assertEqual([job.guild_id for job in claimed], [guild_id])
        self.assertTrue(await restored.scheduler_store.list_jobs(guild_id=88))

    async def test_same_template_uses_independent_resources_permissions_and_previews(self):
        self.store.provision(2)
        first, first_owner = self.guild(1)
        second, second_owner = self.guild(2)
        second.me.guild_permissions.manage_channels = False
        scope = self.store.scope
        template = server_templates.GAMERHQ
        definitions = server_operations.definitions

        def core_sample(guild):
            return [row for row in definitions(guild) if row['name'] in ('community', 'general')]

        async def preview(guild, owner):
            return await server_templates.preview(guild, owner, template)

        with patch.object(server_operations, 'definitions', core_sample):
            with scope(1):
                draft = await preview(first, first_owner)
                result, _, _ = await server_templates.apply(first, first_owner, draft)
                self.assertFalse(result['failed'])
                primary_ids = {channel.id for channel in first.channels}
                self.assertEqual(len(primary_ids), 2)
            with scope(2):
                with self.assertRaises(PermissionError):
                    await server_templates.apply(first, first_owner, draft)
                no_rights = await preview(second, second_owner)
                self.assertTrue(any('Manage Channels' in row.get('reason', '') for row in no_rights['structure']['rows']))
                self.assertFalse(second.channels)
                second.me.guild_permissions.manage_channels = True
                allowed = await preview(second, second_owner)
                result, _, _ = await server_templates.apply(second, second_owner, allowed)
                self.assertFalse(result['failed'])
                secondary_ids = {channel.id for channel in second.channels}
                self.assertEqual(len(secondary_ids), 2)
            self.assertTrue(primary_ids.isdisjoint(secondary_ids))
            for guild, owner, expected in ((first, first_owner, primary_ids), (second, second_owner, secondary_ids)):
                with scope(guild.id):
                    repeat = await preview(guild, owner)
                    result, _, _ = await server_templates.apply(guild, owner, repeat)
                    self.assertEqual((result['created'], result['reused'], result['failed']), ([], [], []))
                    self.assertEqual({channel.id for channel in guild.channels}, expected)

    async def test_reviewed_event_manager_wheel_cannot_cross_tenant_storage(self):
        pytest.importorskip("gamerhq_skill_event_manager")
        self.store.provision(2)
        guilds = {guild_id: SimpleNamespace(id=guild_id) for guild_id in (1, 2)}
        runtime = GamerHQSkillRuntime(SimpleNamespace(get_guild=guilds.get))
        report = load_external_skill_packages(runtime, ("event-manager",))
        self.assertEqual(report.loaded, ("event-manager",))
        await runtime.register_all()

        async def manage(guild_id, name, payload):
            return await runtime.call_management(guild_id=guild_id, skill_id="event-manager",
                                                 contract_id=f"event-manager.{name}.v1", payload=payload)

        with self.store.scope(1):
            self.assertTrue(await runtime.install_skill(guild_id=1, skill_id="event-manager"))
            event = await manage(1, "create", {"name": "Tenant one", "kind": "recurring",
                "startDate": "2030-01-01", "localTime": "19:00", "timezone": "Europe/Berlin",
                "weekdays": [0, 2], "visibility": "private", "requestId": "tenant-one"})
            self.assertEqual(event["event"]["config"]["visibility"], "private")
        with self.store.scope(2):
            self.assertFalse(owns_events(2))
            self.assertTrue(await runtime.install_skill(guild_id=2, skill_id="event-manager"))
            self.assertEqual((await manage(2, "list", {}))["events"], [])
            with self.assertRaises(PermissionError):
                await manage(1, "list", {})
        with self.store.scope(1):
            self.assertTrue(owns_events(1))
            self.assertEqual(len((await manage(1, "list", {}))["events"]), 1)
