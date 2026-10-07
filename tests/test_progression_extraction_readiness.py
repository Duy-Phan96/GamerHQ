"""Host integration gates for the external Progression Skill."""
from __future__ import annotations

from importlib import metadata
from pathlib import Path
import unittest

from hosts.gamerhq.skill_packages import BUNDLED_SKILL_IDS, configured_skill_ids
from skill_runtime.runtime.packages import discover_installed_skills
from cogs import progression_activity as host_adapter

ROOT = Path(__file__).resolve().parents[1]


class ExternalProgressionIntegrationTests(unittest.TestCase):
    def test_progression_is_bundled_external_skill(self):
        self.assertIn("progression", BUNDLED_SKILL_IDS)
        self.assertEqual(configured_skill_ids(()).count("progression"), 1)

    def test_external_package_discovers_with_frozen_identity(self):
        package = discover_installed_skills(("progression",))[0]
        skill = package.skill
        self.assertEqual(package.entry_point, "progression")
        self.assertEqual(skill.manifest.id, "progression")
        self.assertEqual(skill.manifest.version, "0.1.0")
        self.assertEqual(skill.manifest.runtime_api_version, "1")
        self.assertEqual(
            tuple(skill.manifest.permissions),
            (
                "storage.skill",
                "audit.write",
                "discord.messages.send",
                "discord.channels.read",
                "discord.members.read",
                "discord.roles.manage",
            ),
        )

    def test_exactly_one_progression_entry_point_is_installed(self):
        eps = [
            ep for ep in metadata.entry_points(group="gamerhq.skills")
            if ep.name == "progression"
        ]
        self.assertEqual(len(eps), 1)

    def test_host_adapter_uses_only_public_progression_contracts(self):
        source = (ROOT / "cogs" / "progression_activity.py").read_text(encoding="utf-8")
        self.assertNotIn("skills.progression", source)
        self.assertNotIn("gamerhq_skill_xp_progression", source)
        self.assertEqual(host_adapter.GET_CONFIG_API, "progression.get-config.v1")
        self.assertEqual(host_adapter.RECORD_ACTIVITY_API, "progression.record-activity.v1")


if __name__ == "__main__":
    unittest.main()
