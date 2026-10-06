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


class FakeDiscord:
    def __init__(self):
        self.sent = []
        self.roles = set()

    async def get_member(self, *, member_id):
        return SimpleNamespace(id=member_id, display_name="Player One")

    async def grant_role(self, *, member_id, role_id):
        if role_id in self.roles:
            return False
        self.roles.add(role_id)
        return True

    async def remove_role(self, *, member_id, role_id):
        if role_id not in self.roles:
            return False
        self.roles.remove(role_id)
        return True

    async def send_message(self, *, channel_id, content=None, embed=None, allowed_mentions=None):
        self.sent.append({
            "channel_id": channel_id,
            "content": content,
            "allowed_mentions": allowed_mentions,
        })
        return 9001


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
        self.discord = FakeDiscord()
        self.ctx = SimpleNamespace(
            storage=self.storage,
            audit=self.audit,
            discord=self.discord,
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
        response = await self.skill.update_config(
            self.ctx,
            {"config": config, "expectedRevision": 1},
        )
        self.assertEqual(response["config"]["levelCurve"]["maxLevel"], 75)
        self.assertEqual(response["config"]["revision"], 2)
        self.assertEqual(self.storage.data["config.v1"]["levelCurve"]["maxLevel"], 75)
        self.assertEqual(self.audit.calls[0]["action"], "progression.config.updated")

    async def test_stale_config_revision_is_rejected(self):
        await self.skill.enable(self.ctx)
        first = deepcopy(DEFAULT_CONFIG)
        first["levelCurve"]["maxLevel"] = 75
        await self.skill.update_config(
            self.ctx,
            {"config": first, "expectedRevision": 1},
        )

        stale = deepcopy(DEFAULT_CONFIG)
        stale["levelCurve"]["maxLevel"] = 50
        with self.assertRaisesRegex(ValueError, "changed"):
            await self.skill.update_config(
                self.ctx,
                {"config": stale, "expectedRevision": 1},
            )

        current = await self.skill.get_config(self.ctx, {})
        self.assertEqual(current["config"]["levelCurve"]["maxLevel"], 75)

    async def test_record_activity_awards_xp_and_enforces_daily_cap(self):
        await self.skill.enable(self.ctx)
        config = deepcopy(DEFAULT_CONFIG)
        config["xpSources"]["voice"]["dailyCap"] = 10
        await self.skill.update_config(self.ctx, {"config": config})

        first = await self.skill.record_activity(
            self.ctx,
            {"memberId": 7, "source": "voice", "units": 1, "occurredAt": 1_790_000_000},
        )
        second = await self.skill.record_activity(
            self.ctx,
            {"memberId": 7, "source": "voice", "units": 2, "occurredAt": 1_790_000_001},
        )

        self.assertEqual(first["awardedXp"], 5)
        self.assertEqual(second["awardedXp"], 5)
        self.assertTrue(second["dailyCapReached"])

        status = await self.skill.member_status(self.ctx, {"memberId": 7})
        self.assertEqual(status["totalXp"], 10)
        self.assertEqual(status["sourceXp"]["voice"], 10)
        self.assertEqual(status["metrics"]["voiceMinutes"], 30)

    async def test_dedupe_key_blocks_duplicate_event_award(self):
        await self.skill.enable(self.ctx)
        payload = {
            "memberId": 7,
            "source": "lfgParticipation",
            "units": 1,
            "occurredAt": 1_790_000_000,
            "dedupeKey": "lfg:55:lfgParticipation:7",
        }
        first = await self.skill.record_activity(self.ctx, payload)
        second = await self.skill.record_activity(self.ctx, payload)

        self.assertEqual(first["awardedXp"], 25)
        self.assertEqual(second["awardedXp"], 0)
        self.assertEqual(second["reason"], "duplicate")

    async def test_first_mate_and_event_regular_unlock_once(self):
        await self.skill.enable(self.ctx)
        unlocked = []
        for index in range(10):
            response = await self.skill.record_activity(
                self.ctx,
                {
                    "memberId": 7,
                    "source": "lfgParticipation",
                    "units": 1,
                    "occurredAt": 1_790_000_000 + index,
                    "dedupeKey": f"lfg:{index}:lfgParticipation:7",
                },
            )
            unlocked.extend(item["id"] for item in response["unlockedAchievements"])

        self.assertEqual(unlocked.count("first-mate"), 1)
        self.assertEqual(unlocked.count("event-regular"), 1)
        status = await self.skill.member_status(self.ctx, {"memberId": 7})
        self.assertIn("first-mate", status["achievements"])
        self.assertIn("event-regular", status["achievements"])

    async def test_community_host_unlocks_after_ten_completed_events(self):
        await self.skill.enable(self.ctx)
        unlocked = []
        for index in range(10):
            response = await self.skill.record_activity(
                self.ctx,
                {
                    "memberId": 9,
                    "source": "eventHost",
                    "units": 1,
                    "occurredAt": 1_790_000_000 + index,
                    "dedupeKey": f"lfg:{index}:eventHost:9",
                },
            )
            unlocked.extend(item["id"] for item in response["unlockedAchievements"])
        self.assertEqual(unlocked.count("community-host"), 1)

    async def test_voice_milestones_use_active_minutes(self):
        await self.skill.enable(self.ctx)
        response = None
        for index in range(30):
            response = await self.skill.record_activity(
                self.ctx,
                {
                    "memberId": 11,
                    "source": "voice",
                    "units": 1,
                    "occurredAt": 1_790_000_000 + index * 86400,
                    "dedupeKey": f"voice:{index}:11",
                },
            )
        self.assertIn(
            "voice-rookie",
            [item["id"] for item in response["unlockedAchievements"]],
        )

    async def test_disabled_source_awards_nothing(self):
        await self.skill.enable(self.ctx)
        config = deepcopy(DEFAULT_CONFIG)
        config["xpSources"]["voice"]["enabled"] = False
        await self.skill.update_config(self.ctx, {"config": config})

        result = await self.skill.record_activity(
            self.ctx,
            {"memberId": 7, "source": "voice", "units": 1, "occurredAt": 1_790_000_000},
        )

        self.assertEqual(result["awardedXp"], 0)
        self.assertEqual(result["reason"], "disabled")

    async def test_badge_title_and_xp_bonus_rewards_execute_once(self):
        await self.skill.enable(self.ctx)
        config = deepcopy(DEFAULT_CONFIG)
        config["rewards"] = [{
            "id": "starter-pack",
            "name": "Starter Pack",
            "trigger": {"type": "xp", "xp": 5},
            "grants": [
                {"type": "badge", "badgeId": "starter"},
                {"type": "title", "title": "Rising Player"},
                {"type": "xp_bonus", "xp": 20},
            ],
            "enabled": True,
        }]
        await self.skill.update_config(self.ctx, {"config": config})

        first = await self.skill.record_activity(
            self.ctx,
            {"memberId": 7, "source": "voice", "units": 1, "occurredAt": 1_790_000_000},
        )
        second = await self.skill.record_activity(
            self.ctx,
            {"memberId": 7, "source": "voice", "units": 1, "occurredAt": 1_790_000_100},
        )
        status = await self.skill.member_status(self.ctx, {"memberId": 7})

        self.assertEqual(len(first["rewardEvents"]), 1)
        self.assertEqual(second["rewardEvents"], ())
        self.assertIn("starter", status["badges"])
        self.assertIn("Rising Player", status["titles"])
        self.assertIn("starter-pack", status["claimedRewards"])
        self.assertEqual(status["sourceXp"]["reward"], 20)

    async def test_role_reward_tracks_only_skill_owned_grant(self):
        await self.skill.enable(self.ctx)
        config = deepcopy(DEFAULT_CONFIG)
        config["rewards"] = [{
            "id": "veteran-role",
            "name": "Veteran Role",
            "trigger": {"type": "xp", "xp": 5},
            "grants": [{"type": "role", "roleId": 123}],
            "enabled": True,
        }]
        await self.skill.update_config(self.ctx, {"config": config})

        response = await self.skill.record_activity(
            self.ctx,
            {"memberId": 7, "source": "voice", "units": 1, "occurredAt": 1_790_000_000},
        )
        status = await self.skill.member_status(self.ctx, {"memberId": 7})

        self.assertEqual(response["rewardEvents"][0]["grants"][0]["owned"], True)
        self.assertIn("veteran-role:123", status["ownedRoleGrants"])
        self.assertIn("veteran-role", status["claimedRewards"])

    async def test_preexisting_role_reward_is_not_owned(self):
        await self.skill.enable(self.ctx)
        self.discord.roles.add(123)
        config = deepcopy(DEFAULT_CONFIG)
        config["rewards"] = [{
            "id": "existing-role",
            "name": "Existing Role",
            "trigger": {"type": "xp", "xp": 5},
            "grants": [{"type": "role", "roleId": 123}],
            "enabled": True,
        }]
        await self.skill.update_config(self.ctx, {"config": config})

        response = await self.skill.record_activity(
            self.ctx,
            {"memberId": 7, "source": "voice", "units": 1, "occurredAt": 1_790_000_000},
        )
        status = await self.skill.member_status(self.ctx, {"memberId": 7})

        self.assertFalse(response["rewardEvents"][0]["grants"][0]["owned"])
        self.assertNotIn("existing-role:123", status["ownedRoleGrants"])
        self.assertIn("existing-role", status["claimedRewards"])

    async def test_achievement_announcement_uses_configured_channel_and_template(self):
        await self.skill.enable(self.ctx)
        config = deepcopy(DEFAULT_CONFIG)
        config["announcements"] = {
            "enabled": True,
            "channelId": 555,
            "levelUp": False,
            "achievement": True,
            "reward": False,
            "template": "{achievement_emoji} {member} unlocked {achievement_name} at level {level}! +{xp} XP",
        }
        await self.skill.update_config(self.ctx, {"config": config})

        response = await self.skill.record_activity(
            self.ctx,
            {
                "memberId": 7,
                "source": "lfgParticipation",
                "units": 1,
                "occurredAt": 1_790_000_000,
                "dedupeKey": "lfg:1:lfgParticipation:7",
            },
        )

        self.assertTrue(response["announcementSent"])
        self.assertEqual(self.discord.sent[0]["channel_id"], 555)
        self.assertIn("First Mate", self.discord.sent[0]["content"])
        self.assertIn("Player One", self.discord.sent[0]["content"])

    async def test_member_status_never_posts_announcement(self):
        await self.skill.enable(self.ctx)
        config = deepcopy(DEFAULT_CONFIG)
        config["announcements"]["enabled"] = True
        config["announcements"]["channelId"] = 555
        await self.skill.update_config(self.ctx, {"config": config})

        await self.skill.member_status(self.ctx, {"memberId": 7})

        self.assertEqual(self.discord.sent, [])

    async def test_preview_level_is_deterministic(self):
        await self.skill.enable(self.ctx)
        response = await self.skill.preview_level(self.ctx, {"totalXp": 122})
        self.assertEqual(response["level"], 2)
        self.assertEqual(response["currentLevelXp"], 0)


if __name__ == "__main__":
    unittest.main()
