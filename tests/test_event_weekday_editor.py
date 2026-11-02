import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from cogs import lfg


class EventWeekdayEditorTests(unittest.IsolatedAsyncioTestCase):
    def builder(self):
        builder = lfg.EventBuilderView(host=SimpleNamespace(id=10))
        builder.date = "2030-01-07"  # Monday
        builder.hour, builder.minute = 19, 0
        builder.time_12h, builder.period = "07:00", "PM"
        return builder

    def interaction(self, user=10):
        return SimpleNamespace(user=SimpleNamespace(id=user), response=SimpleNamespace(
            edit_message=AsyncMock(), send_message=AsyncMock()))

    async def test_exactly_two_primary_event_types_and_seven_weekday_controls(self):
        builder = self.builder()
        selector = lfg.BuilderRecurrenceSelect(builder)
        self.assertEqual([(option.value, option.label) for option in selector.options],
                         [("once", "One-time Event"), ("recurring", "Recurring Event")])
        view = lfg.WeekdayEditorView(builder)
        toggles = [item for item in view.children if isinstance(item, lfg.WeekdayToggle)]
        self.assertEqual([item.label for item in toggles],
                         ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
        await toggles[2].callback(self.interaction())
        await next(item for item in view.children if isinstance(item, lfg.WeekdayToggle) and item.day == 4).callback(self.interaction())
        self.assertEqual(view.selected, {0, 2, 4})
        self.assertEqual(builder.recurrence_mode, "once")  # draft only until Apply
        await view.apply_days.callback(self.interaction())
        self.assertEqual(builder._recurrence().weekdays, (0, 2, 4))
        self.assertIsNone(builder._recurrence().occurrence_limit)
        self.assertIsNone(builder._recurrence().until_at)

    async def test_empty_weekdays_and_non_owner_cannot_apply(self):
        builder = self.builder()
        view = lfg.WeekdayEditorView(builder)
        view.selected.clear()
        interaction = self.interaction()
        await view.apply_days.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        self.assertEqual(builder.recurrence_mode, "once")
        view.selected = {1}
        denied = self.interaction(user=20)
        await view.apply_days.callback(denied)
        self.assertEqual(builder.recurrence_mode, "once")
        denied.response.edit_message.assert_not_awaited()

    async def test_back_preserves_previous_draft_and_mismatched_start_cannot_persist(self):
        builder = self.builder()
        view = lfg.WeekdayEditorView(builder)
        view.selected = {2}
        await view.back.callback(self.interaction())
        self.assertEqual(builder.recurrence_mode, "once")
        builder.recurrence_mode = "recurring"
        builder.recurrence_weekdays = (2,)
        first = int(datetime(2030, 1, 7, 19, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
        with patch.object(lfg.db, "create_lfg_event_series") as create:
            with self.assertRaisesRegex(ValueError, "first date"):
                lfg.persist_event_draft(guild_id=1, builder=builder, start_at=first)
        create.assert_not_called()
