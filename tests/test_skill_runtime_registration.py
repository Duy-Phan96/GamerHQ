import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from database import db
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.lifecycle import SkillHealth
from skill_runtime.contracts.management import ManagementApiContract
from skill_runtime.contracts.management_ui import (
    ManagementDocumentBinding,
    ManagementField,
    ManagementSection,
    ManagementUiSchema,
)
from skill_runtime.contracts.manifest import SkillManagementApis, SkillManifest
from skill_runtime.contracts.schedule import OnceSchedule


class RegistrationFixtureSkill:
    manifest = SkillManifest(
        id="registration-fixture",
        name="Registration Fixture",
        version="1.0.0",
        runtime_api_version="1",
        description="fixture",
        author="test",
        permissions=(SkillCapability.SCHEDULER_JOBS.value,),
    )

    def __init__(self):
        self.calls = []

    async def register(self, ctx):
        self.calls.append(("register", ctx.skill_id))
        ctx.scheduler.register_handler("registration-fixture.execute.v1", self.execute)

    async def execute(self, ctx, job):
        self.calls.append(("execute", ctx.guild_id, ctx.skill_id, job.key))

    async def enable(self, ctx): pass
    async def disable(self, ctx): pass
    async def start(self, ctx): pass
    async def stop(self, ctx): pass
    async def health_check(self, ctx): return SkillHealth("PASS", "ok")


class ManagementFixtureSkill:
    READ_API = "management-fixture.read.v1"
    WRITE_API = "management-fixture.write.v1"

    manifest = SkillManifest(
        id="management-fixture",
        name="Management Fixture",
        version="1.0.0",
        runtime_api_version="1",
        description="fixture",
        author="test",
        management_apis=SkillManagementApis(
            exposes=(
                ManagementApiContract(READ_API, "Read fixture config."),
                ManagementApiContract(WRITE_API, "Write fixture config."),
            ),
        ),
        management_ui=ManagementUiSchema(
            version="1",
            read_contract=READ_API,
            write_contract=WRITE_API,
            document=ManagementDocumentBinding(
                read_path="config",
                write_path="config",
            ),
            sections=(
                ManagementSection(
                    id="general",
                    title="General",
                    fields=(
                        ManagementField(
                            key="enabled",
                            label="Enabled",
                            type="boolean",
                            config_path="enabled",
                        ),
                    ),
                ),
            ),
        ),
    )

    async def register(self, ctx):
        ctx.management.expose(self.READ_API, self.read_config)
        ctx.management.expose(self.WRITE_API, self.write_config)

    async def read_config(self, ctx, payload):
        return {"config": {"enabled": False}}

    async def write_config(self, ctx, payload):
        return {"config": dict(payload.get("config") or {})}

    async def enable(self, ctx): pass
    async def disable(self, ctx): pass
    async def start(self, ctx): pass
    async def stop(self, ctx): pass
    async def health_check(self, ctx): return SkillHealth("PASS", "ok")


class SkillRuntimeRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "runtime.db")
        self.db_patch.start()
        db.init_db()
        self.guild = SimpleNamespace(id=123)
        self.bot = SimpleNamespace(
            guilds=[self.guild],
            get_guild=lambda guild_id: self.guild if guild_id == self.guild.id else None,
        )
        self.runtime = GamerHQSkillRuntime(self.bot)
        self.skill = RegistrationFixtureSkill()
        self.runtime.register(self.skill)

    async def asyncTearDown(self):
        await self.runtime.close()
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_registration_tracks_builtin_and_external_provenance(self):
        self.assertEqual(
            self.runtime.source("registration-fixture"),
            ("built-in", None),
        )

        external = RegistrationFixtureSkill()
        external.manifest = SkillManifest(
            id="external-fixture",
            name="External Fixture",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(SkillCapability.SCHEDULER_JOBS.value,),
        )
        self.runtime.register(
            external,
            source_kind="external",
            source_distribution="gamerhq-skill-external-fixture",
        )
        self.assertEqual(
            self.runtime.source("external-fixture"),
            ("external", "gamerhq-skill-external-fixture"),
        )

    async def test_status_exposes_skill_provenance(self):
        external = RegistrationFixtureSkill()
        external.manifest = SkillManifest(
            id="external-status",
            name="External Status",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(SkillCapability.SCHEDULER_JOBS.value,),
        )
        self.runtime.register(
            external,
            source_kind="external",
            source_distribution="gamerhq-skill-external-status",
        )
        status = await self.runtime.status(
            guild_id=self.guild.id,
            skill_id="external-status",
        )
        self.assertEqual(status.source_kind, "external")
        self.assertEqual(
            status.source_distribution,
            "gamerhq-skill-external-status",
        )

    async def test_unavailable_external_skill_is_visible_and_cleared_after_registration(self):
        self.runtime.record_external_unavailable("missing-external")
        status = await self.runtime.status(
            guild_id=self.guild.id,
            skill_id="missing-external",
        )
        self.assertEqual(status.health, "UNAVAILABLE")
        self.assertEqual(status.source_kind, "external")
        self.assertFalse(status.enabled)

        external = RegistrationFixtureSkill()
        external.manifest = SkillManifest(
            id="missing-external",
            name="Recovered External",
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=(SkillCapability.SCHEDULER_JOBS.value,),
        )
        self.runtime.register(
            external,
            source_kind="external",
            source_distribution="gamerhq-skill-recovered",
        )
        recovered = await self.runtime.status(
            guild_id=self.guild.id,
            skill_id="missing-external",
        )
        self.assertEqual(recovered.health, "NOT_INSTALLED")
        self.assertFalse(recovered.installed)
        self.assertEqual(recovered.source_distribution, "gamerhq-skill-recovered")

    async def test_statuses_include_configured_unavailable_external_skill(self):
        self.runtime.record_external_unavailable("missing-external")
        statuses = await self.runtime.statuses(guild_id=self.guild.id)
        ids = [status.skill_id for status in statuses]
        self.assertIn("registration-fixture", ids)
        self.assertIn("missing-external", ids)

    async def test_management_write_marks_installed_skill_configured(self):
        skill = ManagementFixtureSkill()
        self.runtime.register(skill)
        await self.runtime.register_all()
        await self.runtime.install_skill(
            guild_id=self.guild.id,
            skill_id=skill.manifest.id,
        )

        status = await self.runtime.status(
            guild_id=self.guild.id,
            skill_id=skill.manifest.id,
        )
        self.assertTrue(status.installed)
        self.assertFalse(status.configured)
        self.assertFalse(status.enabled)

        await self.runtime.call_management(
            guild_id=self.guild.id,
            skill_id=skill.manifest.id,
            contract_id=skill.READ_API,
            payload={},
        )
        after_read = await self.runtime.status(
            guild_id=self.guild.id,
            skill_id=skill.manifest.id,
        )
        self.assertFalse(after_read.configured)

        await self.runtime.call_management(
            guild_id=self.guild.id,
            skill_id=skill.manifest.id,
            contract_id=skill.WRITE_API,
            payload={"config": {"enabled": True}},
        )
        after_write = await self.runtime.status(
            guild_id=self.guild.id,
            skill_id=skill.manifest.id,
        )
        self.assertTrue(after_write.configured)
        self.assertFalse(after_write.enabled)

    async def test_registration_runs_once_and_handler_receives_guild_context(self):
        await self.runtime.register_all()
        await self.runtime.register_all()
        self.assertEqual(
            [call for call in self.skill.calls if call[0] == "register"],
            [("register", "registration-fixture")],
        )

        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="registration-fixture",
            enabled=True,
            version="1.0.0",
        )
        await self.runtime.scheduler.upsert_job(
            guild_id=self.guild.id,
            skill_id="registration-fixture",
            key="fixture",
            handler_id="registration-fixture.execute.v1",
            schedule=OnceSchedule(200),
            payload={},
            now=100,
        )
        report = await self.runtime.scheduler.run_due(now=200)
        self.assertEqual(report.executed, 1)
        self.assertIn(
            ("execute", self.guild.id, "registration-fixture", "fixture"),
            self.skill.calls,
        )

    async def test_unknown_persisted_skill_does_not_block_registered_restore(self):
        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="missing-skill",
            enabled=True,
            version="1.0.0",
        )
        await self.runtime.state.set_enabled(
            guild_id=self.guild.id,
            skill_id="registration-fixture",
            enabled=True,
            version="1.0.0",
        )
        started = await self.runtime.restore_guild(guild_id=self.guild.id)
        self.assertEqual(started, ("registration-fixture",))

    async def test_scheduler_background_lifecycle_is_idempotent(self):
        self.assertTrue(await self.runtime.start_scheduler(poll_seconds=1))
        self.assertFalse(await self.runtime.start_scheduler(poll_seconds=1))
        self.assertTrue(await self.runtime.stop_scheduler())
        self.assertFalse(await self.runtime.stop_scheduler())


if __name__ == "__main__":
    unittest.main()
