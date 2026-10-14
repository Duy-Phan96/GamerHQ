import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs import progression_activity as progression




class ProgressionVoiceEligibilityTests(unittest.TestCase):
    def member(self, member_id, *, bot=False):
        return SimpleNamespace(id=member_id, bot=bot)

    def test_requires_two_humans(self):
        guild = SimpleNamespace(afk_channel=None)
        channel = SimpleNamespace(members=[self.member(1)])
        self.assertEqual(progression.eligible_voice_members(guild, channel), ())

        channel.members.append(self.member(2))
        self.assertEqual(
            tuple(member.id for member in progression.eligible_voice_members(guild, channel)),
            (1, 2),
        )

    def test_bots_do_not_make_voice_eligible(self):
        guild = SimpleNamespace(afk_channel=None)
        channel = SimpleNamespace(members=[self.member(1), self.member(99, bot=True)])
        self.assertEqual(progression.eligible_voice_members(guild, channel), ())

    def test_afk_channel_is_excluded(self):
        channel = SimpleNamespace(members=[self.member(1), self.member(2)])
        guild = SimpleNamespace(afk_channel=channel)
        self.assertEqual(progression.eligible_voice_members(guild, channel), ())


class ProgressionMemberSignalTests(unittest.IsolatedAsyncioTestCase):
    def role(self, role_id):
        return SimpleNamespace(id=role_id)

    def member(self, guild, member_id=42, *, roles=(), premium_since=None):
        return SimpleNamespace(
            id=member_id,
            guild=guild,
            roles=list(roles),
            premium_since=premium_since,
        )

    @patch.object(progression.db, "get_selectable_games")
    def test_booster_transition_emits_stable_public_signal(self, games):
        games.return_value = []
        booster = self.role(5000)
        guild = SimpleNamespace(id=123, premium_subscriber_role=booster)
        before = self.member(guild, roles=[])
        since = datetime(2026, 10, 14, 10, 30, tzinfo=timezone.utc)
        after = self.member(guild, roles=[booster], premium_since=since)

        payloads = progression.progression_member_signals(before, after, now=999)

        self.assertEqual(len(payloads), 1)
        payload = payloads[0]
        self.assertEqual(payload["provider"], "discord")
        self.assertEqual(payload["signal"], "server_boosted")
        self.assertEqual(payload["memberId"], 42)
        self.assertEqual(payload["occurredAt"], int(since.timestamp()))
        self.assertEqual(
            payload["dedupeKey"],
            f"discord:server_boosted:123:42:{int(since.timestamp())}",
        )

    @patch.object(progression.db, "get_selectable_games")
    def test_non_booster_member_update_emits_no_booster_signal(self, games):
        games.return_value = []
        booster = self.role(5000)
        other = self.role(6000)
        guild = SimpleNamespace(id=123, premium_subscriber_role=booster)
        before = self.member(guild, roles=[])
        after = self.member(guild, roles=[other])

        self.assertEqual(
            progression.progression_member_signals(before, after, now=999),
            (),
        )

    @patch.object(progression.db, "get_selectable_games")
    def test_first_game_transition_emits_once_with_member_stable_key(self, games):
        games.return_value = [
            {"id": 1, "role_id": 7001},
            {"id": 2, "role_id": 7002},
        ]
        guild = SimpleNamespace(id=123, premium_subscriber_role=None)
        before = self.member(guild, roles=[])
        after = self.member(guild, roles=[self.role(7002)])

        payloads = progression.progression_member_signals(before, after, now=555)

        self.assertEqual(payloads, ({
            "memberId": 42,
            "provider": "profile",
            "signal": "first_game_added",
            "dedupeKey": "profile:first_game_added:123:42",
            "occurredAt": 555,
        },))

        later = self.member(guild, roles=[self.role(7001), self.role(7002)])
        self.assertEqual(
            progression.progression_member_signals(after, later, now=556),
            (),
        )

    async def test_signal_delivery_retries_with_identical_dedupe_key(self):
        runtime = SimpleNamespace(
            status=AsyncMock(return_value=SimpleNamespace(enabled=True)),
            call_management=AsyncMock(
                side_effect=[RuntimeError("temporary"), {"reason": "handled"}]
            ),
        )
        bot = SimpleNamespace(skill_runtime=runtime)
        payload = {
            "memberId": 42,
            "provider": "profile",
            "signal": "first_game_added",
            "dedupeKey": "profile:first_game_added:123:42",
            "occurredAt": 555,
        }

        delivered = await progression.emit_progression_signal(
            bot, 123, payload, attempts=2
        )

        self.assertTrue(delivered)
        self.assertEqual(runtime.call_management.await_count, 2)
        first = runtime.call_management.await_args_list[0].kwargs["payload"]
        second = runtime.call_management.await_args_list[1].kwargs["payload"]
        self.assertEqual(first["dedupeKey"], second["dedupeKey"])

    async def test_missing_or_disabled_progression_fails_safe(self):
        self.assertFalse(
            await progression.emit_progression_signal(
                SimpleNamespace(skill_runtime=None),
                123,
                {"provider": "profile", "signal": "first_game_added"},
            )
        )

        runtime = SimpleNamespace(
            status=AsyncMock(return_value=SimpleNamespace(enabled=False)),
            call_management=AsyncMock(),
        )
        self.assertFalse(
            await progression.emit_progression_signal(
                SimpleNamespace(skill_runtime=runtime),
                123,
                {"provider": "profile", "signal": "first_game_added"},
            )
        )
        runtime.call_management.assert_not_awaited()



if __name__ == "__main__":
    unittest.main()
