import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import discord

from services import member_profile_service as profiles


class FakeGuild:
    def __init__(self, roles, booster=None):
        self.id = 1
        self._roles = {role.id: role for role in roles}
        self.premium_subscriber_role = booster

    def get_role(self, role_id):
        return self._roles.get(role_id)


class MemberProfileTests(unittest.TestCase):
    def role(self, role_id, name):
        return SimpleNamespace(id=role_id, name=name)

    def member(self, guild, roles):
        return SimpleNamespace(
            id=42,
            guild=guild,
            roles=roles,
            display_name="Player One",
            name="player-one",
            joined_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            display_avatar=SimpleNamespace(url="https://example.invalid/avatar.png"),
        )

    def managed_rows(self):
        return {
            ("base", "age-25plus"): {"role_id": 10},
            ("base", "gender-male"): {"role_id": 11},
            ("base", "pc"): {"role_id": 12},
        }

    def mapped_role(self, kind, key):
        return self.managed_rows().get((kind, key))

    @patch.object(profiles.db, "get_selectable_games")
    @patch.object(profiles.db, "get_managed_role_by_key")
    def test_projects_only_allowlisted_mapped_state(self, managed, games):
        managed.side_effect = self.mapped_role
        games.return_value = [
            {"id": 2, "name": "Overwatch 2", "role_id": 21},
            {"id": 1, "name": "Elden Ring", "role_id": 20},
            {"id": 3, "name": "Missing Game", "role_id": 99},
        ]

        age = self.role(10, "25+")
        gender = self.role(11, "Male")
        platform = self.role(12, "PC")
        elden = self.role(20, "Elden Ring")
        overwatch = self.role(21, "Overwatch 2")
        unknown = self.role(999, "Secret Admin Role")
        booster = self.role(30, "Server Booster")
        guild = FakeGuild(
            [age, gender, platform, elden, overwatch, unknown, booster],
            booster=booster,
        )
        member = self.member(
            guild,
            [age, gender, platform, elden, overwatch, unknown, booster],
        )

        profile = profiles.project_member_profile(member)

        self.assertEqual(profile.age, ("25+",))
        self.assertEqual(profile.gender, ("Male",))
        self.assertEqual(profile.platforms, ("PC",))
        self.assertEqual(profile.games, ("Elden Ring", "Overwatch 2"))
        self.assertTrue(profile.server_booster)
        self.assertNotIn("Secret Admin Role", repr(profile))

    @patch.object(profiles.db, "get_selectable_games", return_value=[])
    @patch.object(profiles.db, "get_managed_role_by_key", return_value=None)
    def test_missing_optional_state_is_graceful(self, managed, games):
        guild = FakeGuild([])
        member = self.member(guild, [])

        profile = profiles.project_member_profile(member)
        embed = profiles.profile_embed(member, profile)

        self.assertEqual(profile.age, ())
        self.assertEqual(profile.gender, ())
        self.assertEqual(profile.platforms, ())
        self.assertEqual(profile.games, ())
        self.assertFalse(profile.server_booster)
        self.assertIn("Member since", embed.fields[0].value)

    @patch.object(profiles.db, "get_selectable_games")
    @patch.object(profiles.db, "get_managed_role_by_key", return_value=None)
    def test_deleted_game_role_is_not_rendered(self, managed, games):
        games.return_value = [{"id": 1, "name": "Ghost Game", "role_id": 55}]
        stale = self.role(55, "Old Game Role")
        guild = FakeGuild([])
        member = self.member(guild, [stale])

        profile = profiles.project_member_profile(member)

        self.assertEqual(profile.games, ())

    @patch.object(profiles.db, "get_selectable_games", return_value=[])
    @patch.object(profiles.db, "get_managed_role_by_key")
    def test_role_values_follow_declared_profile_order(self, managed, games):
        mappings = {
            ("base", "pc"): {"role_id": 12},
            ("base", "xbox"): {"role_id": 13},
        }
        managed.side_effect = lambda kind, key: mappings.get((kind, key))
        pc = self.role(12, "PC")
        xbox = self.role(13, "Xbox")
        guild = FakeGuild([pc, xbox])
        member = self.member(guild, [xbox, pc])

        profile = profiles.project_member_profile(member)

        self.assertEqual(profile.platforms, ("PC", "Xbox"))


if __name__ == "__main__":
    unittest.main()
