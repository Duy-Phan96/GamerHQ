import unittest

import test_role_settings as fixtures
from database import db
from services import member_onboarding_service as onboarding
from services import role_service as roles


class MemberOnboardingServiceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.RoleSettingsTests.setUp
    asyncSetUp = fixtures.RoleSettingsTests.asyncSetUp

    def test_age_question_uses_narrow_optional_bands(self):
        question = next(q for q in onboarding.questions_for_guild(self.guild) if q.key == "age")
        self.assertFalse(question.required)
        self.assertFalse(question.multiple)
        self.assertTrue(question.before_join)
        self.assertEqual(
            [answer.label for answer in question.answers],
            [
                "🔹 Under 18",
                "🔹 18–20",
                "🔹 21–22",
                "🔹 23–24",
                "🔹 25+",
            ],
        )

    def test_gender_is_optional_and_not_forced_before_join(self):
        question = next(q for q in onboarding.questions_for_guild(self.guild) if q.key == "gender")
        self.assertFalse(question.required)
        self.assertFalse(question.before_join)
        self.assertEqual(question.answers[-1].key, "gender-unspecified")
        self.assertIn("Prefer not to say", question.answers[-1].label)

    def test_games_question_is_bounded_and_points_to_full_selector(self):
        question = next(q for q in onboarding.questions_for_guild(self.guild) if q.key == "games")
        self.assertTrue(question.multiple)
        self.assertLessEqual(len(question.answers), 9)
        self.assertEqual(question.answers[-1].key, "games:more")
        self.assertIn("full GamerHQ game library", question.answers[-1].description)

    def test_popular_games_use_member_count_then_name(self):
        from config import DISPLAY_GROUP_ORDER
        first = db.upsert_custom_game("Alpha", "🎮", DISPLAY_GROUP_ORDER[0])
        second = db.upsert_custom_game("Beta", "🎮", DISPLAY_GROUP_ORDER[0])
        for game, count in ((first, 1), (second, 3)):
            db.set_game_selectable(game["id"], True)
            role = self.guild.role(2000 + game["id"])
            role.members = [object()] * count
            self.guild.roles.append(role)
            db.set_game_role(game["id"], role.id)
        names = [game["name"] for game in onboarding.popular_games(self.guild, limit=3)]
        self.assertEqual(names[:2], ["Beta", "Alpha"])

    def test_legacy_age_mapping_is_reported_without_guessing_replacement(self):
        legacy = self.guild.role(9999)
        legacy.name = "18–24"
        self.guild.roles.append(legacy)
        db.upsert_managed_role(
            role_id=legacy.id,
            role_kind="base",
            role_key="age-18-24",
            role_group="Age group",
        )
        status = onboarding.profile_status(self.guild)
        self.assertIn("age-18-24", status["legacy"])
        self.assertNotIn("age-18-24", roles.personal_keys())


if __name__ == "__main__":
    unittest.main()
