import tempfile
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from database import db
from services.event_recurrence_service import (
    Recurrence,
    occurrence_times,
    recurrence_summary,
    resolve_local_wall_time,
)


class EventRecurrenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "events.db")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        db.init_db()

    def test_daily_and_weekly_occurrences_keep_local_wall_time(self):
        tz = ZoneInfo("Europe/Berlin")
        first = int(datetime(2030, 3, 25, 20, 30, tzinfo=tz).timestamp())

        daily = occurrence_times(
            first,
            Recurrence("daily", "Europe/Berlin", occurrence_limit=3),
        )
        self.assertEqual(len(daily), 3)
        self.assertEqual(
            [(datetime.fromtimestamp(ts, tz).hour, datetime.fromtimestamp(ts, tz).minute) for ts in daily],
            [(20, 30), (20, 30), (20, 30)],
        )

        weekly = occurrence_times(
            first,
            Recurrence("weekly", "Europe/Berlin", occurrence_limit=3),
        )
        self.assertEqual(
            [datetime.fromtimestamp(ts, tz).date().isoformat() for ts in weekly],
            ["2030-03-25", "2030-04-01", "2030-04-08"],
        )

    def test_selected_weekdays_are_sorted_deduplicated_and_respected(self):
        tz = ZoneInfo("Europe/Berlin")
        first = int(datetime(2030, 1, 7, 19, 0, tzinfo=tz).timestamp())  # Monday
        spec = Recurrence("weekdays", "Europe/Berlin", weekdays=(4, 0, 4), occurrence_limit=4).validate()
        self.assertEqual(spec.weekdays, (0, 4))

        values = occurrence_times(first, spec)
        self.assertEqual(
            [datetime.fromtimestamp(ts, tz).weekday() for ts in values],
            [0, 4, 0, 4],
        )
        self.assertIn("Mon, Fri", recurrence_summary(spec))

    def test_dst_spring_gap_moves_to_first_valid_local_time(self):
        resolved = resolve_local_wall_time(
            datetime(2030, 3, 31).date(),
            time(2, 30),
            "Europe/Berlin",
        )
        self.assertEqual((resolved.hour, resolved.minute), (3, 0))

    def test_dst_fall_overlap_uses_first_occurrence(self):
        resolved = resolve_local_wall_time(
            datetime(2030, 10, 27).date(),
            time(2, 30),
            "Europe/Berlin",
        )
        self.assertEqual(resolved.fold, 0)
        self.assertEqual(resolved.utcoffset().total_seconds(), 7200)

    def test_until_at_and_materialization_limit_bound_the_plan(self):
        tz = ZoneInfo("Europe/Berlin")
        first_dt = datetime(2030, 1, 1, 18, 0, tzinfo=tz)
        first = int(first_dt.timestamp())
        until_at = int(datetime(2030, 1, 3, 23, 0, tzinfo=tz).timestamp())
        values = occurrence_times(
            first,
            Recurrence("daily", "Europe/Berlin", until_at=until_at),
            limit=20,
        )
        self.assertEqual(len(values), 3)
        self.assertEqual(len(occurrence_times(first, Recurrence("daily", "Europe/Berlin"), limit=2)), 2)

    def test_invalid_recurrence_is_rejected(self):
        invalid = [
            Recurrence("monthly", "Europe/Berlin"),
            Recurrence("weekdays", "Europe/Berlin"),
            Recurrence("weekly", "Europe/Berlin", weekdays=(1,)),
            Recurrence("weekdays", "Europe/Berlin", weekdays=(7,)),
            Recurrence("daily", "Missing/Timezone"),
            Recurrence("daily", "Europe/Berlin", occurrence_limit=0),
        ]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                spec.validate()

    def test_series_storage_is_additive_and_occurrences_can_link_uniquely(self):
        tz = ZoneInfo("Europe/Berlin")
        first = int(datetime(2030, 1, 7, 19, 0, tzinfo=tz).timestamp())
        series = db.create_lfg_event_series(
            guild_id=1,
            host_id=10,
            title="Weekly Raid",
            timezone_name="Europe/Berlin",
            frequency="weekly",
            weekdays=(),
            first_start_at=first,
            occurrence_limit=4,
            max_players=6,
            invite_lead_minutes=30,
        )
        self.assertEqual(series["weekdays"], ())
        self.assertEqual(series["status"], "active")

        event = db.create_lfg_event(
            guild_id=1,
            host_id=10,
            title="Weekly Raid",
            start_at=first,
            max_players=6,
            invite_lead_minutes=30,
            series_id=series["id"],
            occurrence_index=0,
        )
        self.assertEqual(event["series_id"], series["id"])
        self.assertEqual(db.get_lfg_series_occurrences(series["id"])[0]["id"], event["id"])

        with self.assertRaises(Exception):
            db.create_lfg_event(
                guild_id=1,
                host_id=10,
                title="Duplicate",
                start_at=first,
                max_players=6,
                invite_lead_minutes=30,
                series_id=series["id"],
                occurrence_index=0,
            )

        db.init_db()
        self.assertEqual(db.get_lfg_event_series(series["id"])["title"], "Weekly Raid")
        self.assertTrue(db.set_lfg_event_series_status(series["id"], "cancelled"))
        self.assertEqual(db.get_lfg_event_series(series["id"])["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
