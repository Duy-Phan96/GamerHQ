import unittest
from types import SimpleNamespace

from cogs.progression_activity import eligible_voice_members


class ProgressionVoiceEligibilityTests(unittest.TestCase):
    def member(self, member_id, *, bot=False):
        return SimpleNamespace(id=member_id, bot=bot)

    def test_requires_two_humans(self):
        guild = SimpleNamespace(afk_channel=None)
        channel = SimpleNamespace(members=[self.member(1)])
        self.assertEqual(eligible_voice_members(guild, channel), ())

        channel.members.append(self.member(2))
        self.assertEqual(
            tuple(member.id for member in eligible_voice_members(guild, channel)),
            (1, 2),
        )

    def test_bots_do_not_make_voice_eligible(self):
        guild = SimpleNamespace(afk_channel=None)
        channel = SimpleNamespace(members=[self.member(1), self.member(99, bot=True)])
        self.assertEqual(eligible_voice_members(guild, channel), ())

    def test_afk_channel_is_excluded(self):
        channel = SimpleNamespace(members=[self.member(1), self.member(2)])
        guild = SimpleNamespace(afk_channel=channel)
        self.assertEqual(eligible_voice_members(guild, channel), ())


if __name__ == "__main__":
    unittest.main()
