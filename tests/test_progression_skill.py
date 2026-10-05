import unittest
from copy import deepcopy
from types import SimpleNamespace

from skills.progression import (
    DEFAULT_CONFIG,
    ProgressionSkill,
    cumulative_xp_for_level,
    level_for_xp,
    validate_config,
    xp_to_next,
)


class FakeStorage:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


class FakeAudit:
    def __init__(self):
        self.calls = []

    async def write(self, **kwargs):
        self.calls.append(kwargs)


class ProgressionMathTests(unittest.TestCase):
    def test_default_curve_matches_design(self):
        self.assertEqual(xp_to_next(1), 122)
        self.assertEqual(xp_to_next(2), 148)
        self.assertEqual(xp_to_next(5), 250)
        self.assertEqual(xp_to_next(10), 500)
        self.assertEqual(xp_to_next(20), 1300)
        self.assertEqual(xp_to_next(30), 2500)
        self.assertEqual(xp_to_next(50), 6100)

    def test_level_projection_uses_cumulative_xp(self):
        threshold = cumulative_xp_for_level(10)
        level, current, needed = level_for_xp(threshold)
        self.assertEqual(level, 10)
        self.assertEqual(current, 0)
        self.assertEqual(needed, xp_to_next(10))

    def test_max_level_caps_projection(self):
        config = deepcopy(DEFAULT_CONFIG)
        config["levelCurve"]["maxLevel"] = 3
        total = cumulative_xp_for_level(3, config["levelCurve"]) + 999
        level, current, needed = level_for_xp(total, config["levelCurve"])
        self.assertEqual(level, 3)
        self.assertEqual(needed, 0)
        self.assertEqual(current, 999)


class ProgressionConfigTests(unittest.TestCase):
    def test_server_booster_achievement_name_and_xp_are_stable_defaults(self):
        achievement = next(
            item for item in DEFAULT_CONFIG["achievements"]
            if item["id"] == "server-booster"
        )
        self.assertEqual(achievement["name"], "Server Booster")
        self.assertEqual(achievement["xp"], 250)

    def test_reward_rules_are_configurable_and_validated(self):
        config = deepcopy(DEFAULT_CONFIG)
        config["rewards"] = [{
            "id": "level-10-bronze",
            "name": "Bronze Member",
            "trigger": {"type": "level", "level": 10},
            "grants": [
                {"type": "badge", "badgeId": "bronze"},
                {"type": "role", "roleId": 123},
            ],
            "enabled": True,
        }]
        result = validate_config(config)
        self.assertEqual(result["rewards"][0]["trigger"]["level"], 10)
        self.assertEqual(
            [grant["type"] for grant in result["rewards"][0]["grants"]],
            ["badge", "role"],
        )

    def test_reward_cannot_reference_unknown_achievement(self):
        config = deepcopy(DEFAULT_CONFIG)
        config["rewards"] = [{
            "id": "bad",
            "trigger": {"type": "achievement", "achievementId": "missing"},
            "grants": [{"type": "badge", "badgeId": "x"}],
        }]
        with self.assertRaisesRegex(ValueError, "unknown achievement"):
            validate_config(config)


class ProgressionSkillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.skill = ProgressionSkill()
        self.storage = FakeStorage()
        self.audit = FakeAudit()
        self.ctx = SimpleNamespace(
            storage=self.storage,
            audit=self.audit,
        )

    async def test_enable_seeds_default_configuration_once(self):
        await self.skill.enable(self.ctx)
        first = deepcopy(self.storage.data)
        await self.skill.enable(self.ctx)
        self.assertEqual(first, self.storage.data)

    async def test_management_status_summarizes_config(self):
        await self.skill.enable(self.ctx)
        status = await self.skill.status(self.ctx, {})
        self.assertEqual(status["maxLevel"], 100)
        self.assertGreaterEqual(status["xpSourceCount"], 5)
        self.assertGreaterEqual(status["achievementCount"], 5)
        self.assertEqual(status["rewardCount"], 0)

    async def test_update_config_persists_and_audits(self):
        await self.skill.enable(self.ctx)
        config = deepcopy(DEFAULT_CONFIG)
        config["levelCurve"]["maxLevel"] = 75
        response = await self.skill.update_config(self.ctx, {"config": config})
        self.assertEqual(response["config"]["levelCurve"]["maxLevel"], 75)
        self.assertEqual(self.storage.data["config.v1"]["levelCurve"]["maxLevel"], 75)
        self.assertEqual(self.audit.calls[0]["action"], "progression.config.updated")

    async def test_preview_level_is_deterministic(self):
        await self.skill.enable(self.ctx)
        response = await self.skill.preview_level(self.ctx, {"totalXp": 122})
        self.assertEqual(response["level"], 2)
        self.assertEqual(response["currentLevelXp"], 0)


if __name__ == "__main__":
    unittest.main()
