import tempfile
import unittest
import discord
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from cogs import lfg
from database import db
from services.event_recurrence_service import (
    Recurrence,
    occurrence_times,
    recurrence_summary,
    resolve_local_wall_time,
)
from services.event_series_service import materialize_guild, materialize_series


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

    def test_one_time_draft_creates_only_one_event(self):
        start = int(datetime(2030, 2, 1, 20, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
        builder = SimpleNamespace(
            host=SimpleNamespace(id=10),
            title="One Night",
            max_players=5,
            invite_lead=15,
            visibility="public",
            _recurrence=lambda: None,
        )
        event, series = lfg.persist_event_draft(guild_id=1, builder=builder, start_at=start)
        self.assertIsNone(series)
        self.assertIsNone(event["series_id"])
        self.assertIsNone(event["occurrence_index"])
        self.assertEqual(db.get_active_lfg_event_series(1), [])

    def test_recurring_draft_creates_series_and_first_occurrence(self):
        start = int(datetime(2030, 2, 1, 20, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
        builder = SimpleNamespace(
            host=SimpleNamespace(id=10),
            title="Weekly Night",
            max_players=5,
            invite_lead=15,
            visibility="public",
            _recurrence=lambda: Recurrence("weekly", "Europe/Berlin"),
        )
        event, series = lfg.persist_event_draft(guild_id=1, builder=builder, start_at=start)
        self.assertIsNotNone(series)
        self.assertEqual(series["frequency"], "weekly")
        self.assertEqual(event["series_id"], series["id"])
        self.assertEqual(event["occurrence_index"], 0)
        self.assertEqual([row["id"] for row in db.get_lfg_series_occurrences(series["id"])], [event["id"]])

    def test_failed_occurrence_creation_does_not_leave_empty_series(self):
        start = int(datetime(2030, 2, 1, 20, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
        builder = SimpleNamespace(
            host=SimpleNamespace(id=10),
            title="Broken Series",
            max_players=5,
            invite_lead=15,
            visibility="public",
            _recurrence=lambda: Recurrence("weekly", "Europe/Berlin"),
        )
        with patch.object(db, "create_lfg_event", side_effect=ValueError("failed")):
            with self.assertRaisesRegex(ValueError, "failed"):
                lfg.persist_event_draft(guild_id=1, builder=builder, start_at=start)
        self.assertEqual(db.get_active_lfg_event_series(1), [])

    def test_materialization_is_idempotent_across_retries_and_restart(self):
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
        db.create_lfg_event(
            guild_id=1,
            host_id=10,
            title="Weekly Raid",
            start_at=first,
            max_players=6,
            invite_lead_minutes=30,
            series_id=series["id"],
            occurrence_index=0,
        )

        now = first - 60
        through = int(datetime(2030, 1, 31, 23, 59, tzinfo=tz).timestamp())
        first_run = materialize_series(series, now=now, through_at=through)
        second_run = materialize_series(db.get_lfg_event_series(series["id"]), now=now, through_at=through)
        db.init_db()
        third_run = materialize_guild(1, now=now, through_at=through)

        self.assertEqual(len(first_run), 3)
        self.assertEqual(second_run, [])
        self.assertEqual(third_run, [])
        occurrences = db.get_lfg_series_occurrences(series["id"])
        self.assertEqual([row["occurrence_index"] for row in occurrences], [0, 1, 2, 3])

    def test_materialization_respects_rolling_horizon(self):
        tz = ZoneInfo("Europe/Berlin")
        first = int(datetime(2030, 1, 1, 20, 0, tzinfo=tz).timestamp())
        series = db.create_lfg_event_series(
            guild_id=1,
            host_id=10,
            title="Daily",
            timezone_name="Europe/Berlin",
            frequency="daily",
            weekdays=(),
            first_start_at=first,
            max_players=5,
            invite_lead_minutes=15,
        )
        db.create_lfg_event(
            guild_id=1,
            host_id=10,
            title="Daily",
            start_at=first,
            max_players=5,
            invite_lead_minutes=15,
            series_id=series["id"],
            occurrence_index=0,
        )

        now = first - 60
        through = first + 2 * 86400
        created = materialize_series(series, now=now, through_at=through)
        self.assertEqual([row["occurrence_index"] for row in created], [1, 2])
        self.assertTrue(all(row["start_at"] <= through for row in created))

    def test_materialization_skips_past_missing_occurrences(self):
        tz = ZoneInfo("Europe/Berlin")
        first = int(datetime(2030, 1, 1, 20, 0, tzinfo=tz).timestamp())
        series = db.create_lfg_event_series(
            guild_id=1,
            host_id=10,
            title="Daily",
            timezone_name="Europe/Berlin",
            frequency="daily",
            weekdays=(),
            first_start_at=first,
            occurrence_limit=4,
            max_players=5,
            invite_lead_minutes=15,
        )
        db.create_lfg_event(
            guild_id=1,
            host_id=10,
            title="Daily",
            start_at=first,
            max_players=5,
            invite_lead_minutes=15,
            series_id=series["id"],
            occurrence_index=0,
        )

        now = first + 2 * 86400 + 60
        created = materialize_series(series, now=now, through_at=first + 5 * 86400)
        self.assertEqual([row["occurrence_index"] for row in created], [3])

    async def test_public_occurrence_publication_reuses_existing_message(self):
        start = int(datetime(2030, 2, 1, 20, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
        event = db.create_lfg_event(
            guild_id=1,
            host_id=10,
            title="Weekly Night",
            start_at=start,
            max_players=5,
            invite_lead_minutes=15,
            series_id=1,
            occurrence_index=1,
        )

        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 500
        channel.send = AsyncMock()
        sent = SimpleNamespace(id=900, edit=AsyncMock())
        channel.send.return_value = sent
        existing = SimpleNamespace(id=900, edit=AsyncMock())

        async def empty_history(*args, **kwargs):
            if False:
                yield None

        channel.history = empty_history
        channel.fetch_message = AsyncMock(return_value=existing)

        guild = MagicMock(spec=discord.Guild)
        guild.id = 1
        guild.me = SimpleNamespace(id=999)
        guild.get_member.return_value = None

        with patch.object(lfg, "find_lfg_channel", return_value=channel), \
             patch.object(lfg, "render_event", return_value="card"), \
             patch.object(lfg, "refresh_event_posts", AsyncMock()) as refresh:
            self.assertTrue(await lfg.ensure_event_publication(guild, event))
            self.assertTrue(await lfg.ensure_event_publication(guild, db.get_lfg_event(event["id"])))

        channel.send.assert_awaited_once()
        existing.edit.assert_awaited_once()
        refresh.assert_awaited()
        rows = db.get_lfg_event_messages(event["id"])
        self.assertEqual([(row["channel_id"], row["message_id"]) for row in rows], [(500, 900)])

    async def test_private_occurrence_recovers_channel_by_owned_topic(self):
        start = int(datetime(2030, 2, 1, 20, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
        event = db.create_lfg_event(
            guild_id=1,
            host_id=10,
            title="Private Raid",
            start_at=start,
            max_players=5,
            invite_lead_minutes=15,
            visibility="private",
            series_id=1,
            occurrence_index=1,
        )
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 700
        channel.topic = lfg._private_event_topic(event)

        guild = MagicMock(spec=discord.Guild)
        guild.id = 1
        guild.text_channels = [channel]
        guild.get_channel.return_value = None

        recovered = await lfg.ensure_private_event_channel(guild, event)
        self.assertIs(recovered, channel)
        self.assertEqual(db.get_lfg_event(event["id"])["private_channel_id"], 700)

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
