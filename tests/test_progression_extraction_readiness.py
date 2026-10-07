"""Extraction-readiness gates for the built-in Progression Skill."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from skill_runtime.devtools import require_clean_skill_source, validate_skill_factory
from skills.progression import (
    CONFIG_KEY,
    MEMBER_KEY_PREFIX,
    PROGRESSION_CAPABILITIES,
    PROGRESSION_MANAGEMENT_APIS,
    RUNTIME_API_VERSION,
    SKILL_ID,
    SKILL_VERSION,
    DEFAULT_CONFIG,
    create_skill,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeStorage:
    def __init__(self, values=None):
        self.data = deepcopy(values or {})
        self.writes = 0
        self.deletes = 0

    async def get(self, key):
        return deepcopy(self.data.get(key))

    async def set(self, key, value):
        self.writes += 1
        self.data[key] = deepcopy(value)

    async def delete(self, key):
        self.deletes += 1
        self.data.pop(key, None)


class ProgressionExtractionReadinessTests(unittest.IsolatedAsyncioTestCase):
    def test_portable_source_passes_sdk_source_audit(self):
        report = require_clean_skill_source([ROOT / "skills" / "progression.py"])
        self.assertEqual(report.findings, ())

    def test_factory_and_lifecycle_conform_to_public_sdk(self):
        report = validate_skill_factory(
            create_skill,
            expected_skill_id=SKILL_ID,
        )
        self.assertEqual(report.skill_id, "progression")
        self.assertEqual(report.version, SKILL_VERSION)
        self.assertEqual(report.runtime_api_version, RUNTIME_API_VERSION)

    def test_public_identity_and_management_contracts_are_frozen(self):
        skill = create_skill()
        self.assertEqual(skill.manifest.id, "progression")
        self.assertEqual(skill.manifest.version, "0.1.0")
        self.assertEqual(skill.manifest.runtime_api_version, "1")
        self.assertEqual(
            PROGRESSION_MANAGEMENT_APIS,
            (
                "progression.status.v1",
                "progression.get-config.v1",
                "progression.update-config.v1",
                "progression.preview-level.v1",
                "progression.record-activity.v1",
                "progression.member-status.v1",
            ),
        )
        self.assertEqual(
            tuple(contract.id for contract in skill.manifest.management_apis.exposes),
            PROGRESSION_MANAGEMENT_APIS,
        )

    def test_capability_contract_is_explicit_and_matches_manifest(self):
        skill = create_skill()
        self.assertEqual(tuple(skill.manifest.permissions), PROGRESSION_CAPABILITIES)
        self.assertEqual(
            PROGRESSION_CAPABILITIES,
            (
                "storage.skill",
                "audit.write",
                "discord.messages.send",
                "discord.channels.read",
                "discord.members.read",
                "discord.roles.manage",
            ),
        )

    def test_storage_namespace_is_stable_for_extraction(self):
        self.assertEqual(CONFIG_KEY, "config.v1")
        self.assertEqual(MEMBER_KEY_PREFIX, "member.v1:")

    async def test_disable_and_health_do_not_destroy_or_rewrite_storage(self):
        storage = FakeStorage({CONFIG_KEY: DEFAULT_CONFIG, MEMBER_KEY_PREFIX + "7": {"totalXp": 25}})
        before = deepcopy(storage.data)
        skill = create_skill()
        ctx = SimpleNamespace(storage=storage)

        health = await skill.health_check(ctx)
        await skill.disable(ctx)

        self.assertEqual(health.state, "PASS")
        self.assertEqual(storage.data, before)
        self.assertEqual(storage.writes, 0)
        self.assertEqual(storage.deletes, 0)


if __name__ == "__main__":
    unittest.main()
