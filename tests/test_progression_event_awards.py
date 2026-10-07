import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.progression_activity import award_completed_lfg


class ProgressionEventAwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_event_awards_joined_members_and_host(self):
        runtime = SimpleNamespace(
            status=AsyncMock(return_value=SimpleNamespace(enabled=True)),
            call_management=AsyncMock(),
        )
        bot = SimpleNamespace(skill_runtime=runtime)
        guild = SimpleNamespace(id=1)
        event = {"id": 77, "host_id": 10, "ended_at": 1_790_000_000}

        rows = [
            {"user_id": 10, "status": "joined"},
            {"user_id": 11, "status": "joined"},
            {"user_id": 12, "status": "invited"},
        ]
        with patch("cogs.progression_activity.db.get_lfg_event_members", return_value=rows):
            await award_completed_lfg(bot, guild, event)

        payloads = [call.kwargs["payload"] for call in runtime.call_management.await_args_list]
        self.assertEqual(
            [(p["memberId"], p["source"]) for p in payloads],
            [(10, "lfgParticipation"), (11, "lfgParticipation"), (10, "eventHost")],
        )
        self.assertEqual(len({p["dedupeKey"] for p in payloads}), 3)

    async def test_disabled_progression_skips_event_awards(self):
        runtime = SimpleNamespace(
            status=AsyncMock(return_value=SimpleNamespace(enabled=False)),
            call_management=AsyncMock(),
        )
        await award_completed_lfg(
            SimpleNamespace(skill_runtime=runtime),
            SimpleNamespace(id=1),
            {"id": 77, "host_id": 10, "ended_at": 1_790_000_000},
        )
        runtime.call_management.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
