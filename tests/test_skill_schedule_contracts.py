import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from skill_runtime.contracts.schedule import (
    DailySchedule,
    IntervalSchedule,
    OnceSchedule,
    WeeklySchedule,
    next_run_at,
    schedule_from_dict,
    schedule_to_dict,
)


class ScheduleContractTests(unittest.TestCase):
    def test_interval_has_anti_spam_minimum(self):
        with self.assertRaisesRegex(ValueError, "at least 60"):
            IntervalSchedule(59)
        self.assertEqual(next_run_at(IntervalSchedule(60), after=100), 160)

    def test_once_runs_only_in_future(self):
        self.assertEqual(next_run_at(OnceSchedule(200), after=100), 200)
        self.assertIsNone(next_run_at(OnceSchedule(100), after=100))

    def test_daily_uses_timezone_and_survives_roundtrip(self):
        berlin = ZoneInfo("Europe/Berlin")
        after = int(datetime(2026, 10, 3, 17, 0, tzinfo=berlin).timestamp())
        schedule = DailySchedule(18, 0, "Europe/Berlin")
        nxt = datetime.fromtimestamp(next_run_at(schedule, after=after), berlin)
        self.assertEqual((nxt.hour, nxt.minute), (18, 0))
        self.assertEqual(schedule_from_dict(schedule_to_dict(schedule)), schedule)

    def test_weekly_uses_monday_zero(self):
        berlin = ZoneInfo("Europe/Berlin")
        # Saturday 2026-10-03 -> next Monday.
        after = int(datetime(2026, 10, 3, 12, 0, tzinfo=berlin).timestamp())
        schedule = WeeklySchedule(0, 10, 30, "Europe/Berlin")
        nxt = datetime.fromtimestamp(next_run_at(schedule, after=after), berlin)
        self.assertEqual(nxt.weekday(), 0)
        self.assertEqual((nxt.hour, nxt.minute), (10, 30))

    def test_nonexistent_dst_wall_time_is_skipped(self):
        berlin = ZoneInfo("Europe/Berlin")
        # Europe/Berlin jumps over 02:30 on 2026-03-29.
        after = int(datetime(2026, 3, 28, 23, 0, tzinfo=berlin).timestamp())
        schedule = DailySchedule(2, 30, "Europe/Berlin")
        nxt = datetime.fromtimestamp(next_run_at(schedule, after=after), berlin)
        self.assertEqual(nxt.date().isoformat(), "2026-03-30")
        self.assertEqual((nxt.hour, nxt.minute), (2, 30))

    def test_ambiguous_dst_wall_time_executes_first_occurrence_only(self):
        berlin = ZoneInfo("Europe/Berlin")
        after = int(datetime(2026, 10, 24, 23, 0, tzinfo=berlin).timestamp())
        schedule = DailySchedule(2, 30, "Europe/Berlin")
        first = next_run_at(schedule, after=after)
        local = datetime.fromtimestamp(first, berlin)
        self.assertEqual(local.fold, 0)
        # Asking strictly after the first occurrence skips the second fold and
        # moves to the next day, so one local schedule cannot double-run.
        second = datetime.fromtimestamp(next_run_at(schedule, after=first), berlin)
        self.assertEqual(second.date().isoformat(), "2026-10-26")

    def test_unknown_timezone_and_schedule_type_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Unknown IANA timezone"):
            DailySchedule(12, 0, "Mars/Olympus")
        with self.assertRaisesRegex(ValueError, "Unknown schedule type"):
            schedule_from_dict({"type": "cron", "value": "* * * * *"})


if __name__ == "__main__":
    unittest.main()
