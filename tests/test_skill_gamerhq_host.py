import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database import db
from hosts.gamerhq.skill_host import (
    CapabilityPermissions,
    GamerHQSkillAudit,
    GamerHQSkillStateStore,
    GamerHQSkillStorage,
)
from skill_runtime.contracts.capabilities import SkillCapability


class GamerHQSkillHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "skills.db"
        self.db_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def permissions(self, *capabilities):
        return CapabilityPermissions(capabilities)

    async def test_existing_enablement_rows_migrate_as_installed(self):
        legacy_path = Path(self.temp.name) / "legacy-skills.db"
        self.db_patch.stop()
        with patch.object(db, "DB_PATH", legacy_path):
            import sqlite3

            conn = sqlite3.connect(legacy_path)
            conn.execute(
                """
                CREATE TABLE skill_guild_state (
                    guild_id INTEGER NOT NULL,
                    skill_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    version TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, skill_id)
                )
                """
            )
            conn.execute(
                "INSERT INTO skill_guild_state(guild_id,skill_id,enabled,version,updated_at) "
                "VALUES(1,'recurring-posts',0,'1.2.0',1)"
            )
            conn.execute("PRAGMA user_version=1")
            conn.commit()
            conn.close()

            db.init_db()
            state = GamerHQSkillStateStore()
            self.assertTrue(
                await state.is_installed(
                    guild_id=1,
                    skill_id="recurring-posts",
                )
            )
            self.assertTrue(
                await state.is_configured(
                    guild_id=1,
                    skill_id="recurring-posts",
                )
            )
            self.assertFalse(
                await state.is_enabled(
                    guild_id=1,
                    skill_id="recurring-posts",
                )
            )

        self.db_patch.start()

    async def test_skill_installation_is_separate_from_enablement(self):
        state = GamerHQSkillStateStore()

        self.assertFalse(await state.is_installed(guild_id=1, skill_id="recurring-posts"))
        self.assertTrue(
            await state.install(
                guild_id=1,
                skill_id="recurring-posts",
                version="1.2.1",
            )
        )
        self.assertFalse(
            await state.install(
                guild_id=1,
                skill_id="recurring-posts",
                version="1.2.1",
            )
        )
        self.assertTrue(await state.is_installed(guild_id=1, skill_id="recurring-posts"))
        self.assertFalse(await state.is_configured(guild_id=1, skill_id="recurring-posts"))
        self.assertFalse(await state.is_enabled(guild_id=1, skill_id="recurring-posts"))
        await state.set_configured(
            guild_id=1,
            skill_id="recurring-posts",
            configured=True,
            version="1.2.1",
        )
        self.assertTrue(await state.is_configured(guild_id=1, skill_id="recurring-posts"))
        self.assertEqual(
            await state.installed_skill_ids(guild_id=1),
            ("recurring-posts",),
        )

    async def test_legacy_enable_marks_skill_installed_for_compatibility(self):
        state = GamerHQSkillStateStore()
        await state.set_enabled(
            guild_id=1,
            skill_id="recurring-posts",
            enabled=True,
            version="1.2.1",
        )
        self.assertTrue(await state.is_installed(guild_id=1, skill_id="recurring-posts"))
        self.assertTrue(await state.is_enabled(guild_id=1, skill_id="recurring-posts"))

    async def test_direct_enable_does_not_fake_configured_state(self):
        state = GamerHQSkillStateStore()
        await state.set_enabled(
            guild_id=7,
            skill_id="new-skill",
            enabled=True,
            version="1.0.0",
        )
        self.assertTrue(await state.is_installed(guild_id=7, skill_id="new-skill"))
        self.assertFalse(await state.is_configured(guild_id=7, skill_id="new-skill"))
        self.assertTrue(await state.is_enabled(guild_id=7, skill_id="new-skill"))

    async def test_installed_disabled_skill_is_management_available(self):
        state = GamerHQSkillStateStore()
        await state.install(
            guild_id=1,
            skill_id="recurring-posts",
            version="1.2.1",
        )
        self.assertTrue(
            await state.is_installed(
                guild_id=1,
                skill_id="recurring-posts",
            )
        )
        self.assertFalse(
            await state.is_enabled(
                guild_id=1,
                skill_id="recurring-posts",
            )
        )

    async def test_skill_state_is_per_guild_and_persists_enabled_ids(self):
        state = GamerHQSkillStateStore()
        await state.set_enabled(guild_id=1, skill_id="recurring-posts", enabled=True, version="1.0.0")
        await state.set_enabled(guild_id=1, skill_id="analytics", enabled=False, version="1.2.0")
        await state.set_enabled(guild_id=2, skill_id="analytics", enabled=True, version="1.2.0")

        self.assertTrue(await state.is_enabled(guild_id=1, skill_id="recurring-posts"))
        self.assertFalse(await state.is_enabled(guild_id=1, skill_id="analytics"))
        self.assertEqual(await state.enabled_skill_ids(guild_id=1), ("recurring-posts",))
        self.assertEqual(await state.enabled_skill_ids(guild_id=2), ("analytics",))

    async def test_skill_storage_isolated_by_guild_and_skill(self):
        cap = SkillCapability.STORAGE_SKILL.value
        one = GamerHQSkillStorage(guild_id=1, skill_id="recurring-posts", permissions=self.permissions(cap))
        two = GamerHQSkillStorage(guild_id=1, skill_id="analytics", permissions=self.permissions(cap))
        other_guild = GamerHQSkillStorage(guild_id=2, skill_id="recurring-posts", permissions=self.permissions(cap))

        await one.set("config", {"enabled": True, "count": 3})
        self.assertEqual(await one.get("config"), {"count": 3, "enabled": True})
        self.assertIsNone(await two.get("config"))
        self.assertIsNone(await other_guild.get("config"))

        await two.set("config", {"owner": "analytics"})
        self.assertEqual(await one.get("config"), {"count": 3, "enabled": True})
        self.assertEqual(await two.get("config"), {"owner": "analytics"})

    async def test_storage_requires_declared_capability(self):
        storage = GamerHQSkillStorage(
            guild_id=1,
            skill_id="recurring-posts",
            permissions=self.permissions(),
        )
        with self.assertRaisesRegex(PermissionError, "did not declare"):
            await storage.get("config")
        with self.assertRaisesRegex(PermissionError, "did not declare"):
            await storage.set("config", {"x": 1})

    async def test_storage_key_and_size_limits_fail_closed(self):
        cap = SkillCapability.STORAGE_SKILL.value
        storage = GamerHQSkillStorage(
            guild_id=1,
            skill_id="recurring-posts",
            permissions=self.permissions(cap),
        )
        with self.assertRaisesRegex(ValueError, "storage key"):
            await storage.set("../other-skill", 1)
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            await storage.set("large", "x" * (70 * 1024))

    async def test_storage_delete_only_affects_current_namespace(self):
        cap = SkillCapability.STORAGE_SKILL.value
        a = GamerHQSkillStorage(guild_id=1, skill_id="recurring-posts", permissions=self.permissions(cap))
        b = GamerHQSkillStorage(guild_id=1, skill_id="analytics", permissions=self.permissions(cap))
        await a.set("same", 1)
        await b.set("same", 2)
        await a.delete("same")
        self.assertIsNone(await a.get("same"))
        self.assertEqual(await b.get("same"), 2)

    def test_permissions_require_declared_and_host_available_capability(self):
        cap = SkillCapability.STORAGE_SKILL.value
        permissions = CapabilityPermissions((cap,), available=(cap,))
        self.assertTrue(permissions.allows(cap))
        permissions.require(cap)

        unavailable = CapabilityPermissions((cap,), available=())
        self.assertFalse(unavailable.allows(cap))
        with self.assertRaisesRegex(PermissionError, "Host does not provide"):
            unavailable.require(cap)

        with self.assertRaisesRegex(PermissionError, "Unknown"):
            permissions.require("database.raw")

    async def test_audit_reuses_existing_server_log_and_does_not_emit_metadata_values(self):
        cap = SkillCapability.AUDIT_WRITE.value
        guild = SimpleNamespace(id=123)
        audit = GamerHQSkillAudit(
            guild=guild,
            skill_id="recurring-posts",
            permissions=self.permissions(cap),
        )
        private_value = "private-value-must-not-appear"
        with patch("services.server_log_service.emit", new_callable=AsyncMock, return_value=True) as emit:
            await audit.write(
                action="post.created",
                target="post-123",
                metadata={"schedule": private_value, "channel": private_value},
            )
        args = emit.await_args.args
        self.assertIs(args[0], guild)
        self.assertIn("Skill Activity", args[2])
        self.assertIn("Metadata fields: channel, schedule", args[3])
        self.assertNotIn(private_value, args[3])

    async def test_audit_requires_capability(self):
        guild = SimpleNamespace(id=123)
        audit = GamerHQSkillAudit(guild=guild, skill_id="recurring-posts", permissions=self.permissions())
        with self.assertRaisesRegex(PermissionError, "did not declare"):
            await audit.write(action="post.created")


if __name__ == "__main__":
    unittest.main()
