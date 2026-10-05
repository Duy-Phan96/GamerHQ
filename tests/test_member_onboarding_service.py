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


    def test_question_overrides_persist_without_copying_dynamic_answers(self):
        revision = onboarding.save_question_override(
            self.guild.id,
            "age",
            prompt="Which age group fits you?",
            required=True,
            before_join=False,
            expected_revision=0,
        )
        self.assertEqual(revision, 1)
        question = next(
            q for q in onboarding.questions_for_guild(self.guild, include_disabled=True)
            if q.key == "age"
        )
        self.assertEqual(question.prompt, "Which age group fits you?")
        self.assertTrue(question.required)
        self.assertFalse(question.before_join)
        self.assertEqual(
            [answer.key for answer in question.answers],
            ["age-under18", "age-18-20", "age-21-22", "age-23-24", "age-25plus"],
        )
        raw = db.get_setting(f"member_onboarding_config:{self.guild.id}")
        self.assertNotIn("age-under18", raw)
        self.assertNotIn("Under 18", raw)

    def test_disabled_question_is_hidden_from_preview_but_manageable(self):
        onboarding.save_question_override(
            self.guild.id,
            "gender",
            enabled=False,
            expected_revision=0,
        )
        visible = {q.key for q in onboarding.questions_for_guild(self.guild)}
        managed = {q.key for q in onboarding.questions_for_guild(self.guild, include_disabled=True)}
        self.assertNotIn("gender", visible)
        self.assertIn("gender", managed)

    def test_stale_question_edit_fails_closed(self):
        onboarding.save_question_override(
            self.guild.id,
            "age",
            prompt="Age?",
            expected_revision=0,
        )
        with self.assertRaisesRegex(ValueError, "changed"):
            onboarding.save_question_override(
                self.guild.id,
                "age",
                required=True,
                expected_revision=0,
            )

    def test_reset_defaults_preserves_revision_and_restores_builtin_values(self):
        onboarding.save_question_override(
            self.guild.id,
            "games",
            prompt="Pick your games",
            enabled=False,
            expected_revision=0,
        )
        revision = onboarding.reset_config(self.guild.id, expected_revision=1)
        self.assertEqual(revision, 2)
        config = onboarding.load_config(self.guild.id)
        self.assertEqual(config["revision"], 2)
        self.assertEqual(config["questions"], {})
        question = next(q for q in onboarding.questions_for_guild(self.guild) if q.key == "games")
        self.assertEqual(question.prompt, "What games do you play?")
        self.assertTrue(question.enabled)

    def test_invalid_question_prompt_and_unknown_key_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 3 and 100"):
            onboarding.save_question_override(self.guild.id, "age", prompt="x")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            onboarding.save_question_override(self.guild.id, "not-real", prompt="Valid prompt")


if __name__ == "__main__":
    unittest.main()
