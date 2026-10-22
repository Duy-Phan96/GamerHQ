"""Pure recurring-event domain helpers for GamerHQ Event Manager.

This module deliberately contains no Discord.py imports. It models recurrence in
local wall-clock time so it can later move behind a portable Event Manager Skill
boundary without dragging Host implementation details with it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


VALID_FREQUENCIES = {"daily", "weekly", "weekdays"}


@dataclass(frozen=True)
class Recurrence:
    frequency: str
    timezone_name: str
    weekdays: tuple[int, ...] = ()
    occurrence_limit: int | None = None
    until_at: int | None = None

    def validate(self) -> "Recurrence":
        if self.frequency not in VALID_FREQUENCIES:
            raise ValueError("Recurrence must be daily, weekly, or selected weekdays.")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Choose a valid IANA timezone.") from exc

        days = tuple(sorted(set(int(day) for day in self.weekdays)))
        if any(day < 0 or day > 6 for day in days):
            raise ValueError("Weekdays must use values 0–6 (Monday–Sunday).")
        if self.frequency == "weekdays" and not days:
            raise ValueError("Choose at least one weekday.")
        if self.frequency != "weekdays" and days:
            raise ValueError("Weekdays are only valid for selected-weekday recurrence.")
        if self.occurrence_limit is not None and not 1 <= int(self.occurrence_limit) <= 365:
            raise ValueError("Occurrence count must be between 1 and 365.")
        if self.until_at is not None and int(self.until_at) <= 0:
            raise ValueError("Series end time is invalid.")
        return Recurrence(
            frequency=self.frequency,
            timezone_name=self.timezone_name,
            weekdays=days,
            occurrence_limit=int(self.occurrence_limit) if self.occurrence_limit is not None else None,
            until_at=int(self.until_at) if self.until_at is not None else None,
        )


def _roundtrip_matches(candidate: datetime, naive: datetime, tz: ZoneInfo) -> bool:
    roundtrip = candidate.astimezone(timezone.utc).astimezone(tz)
    return roundtrip.replace(tzinfo=None) == naive


def resolve_local_wall_time(day: date, wall_time: time, timezone_name: str) -> datetime:
    """Resolve a local wall time deterministically across DST transitions.

    Normal and fall-back ambiguous times use fold=0 (the first occurrence).
    Spring-forward nonexistent times move forward minute-by-minute to the first
    valid local instant, preserving the calendar date whenever possible.
    """
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Choose a valid IANA timezone.") from exc

    naive = datetime.combine(day, wall_time.replace(tzinfo=None))
    first = naive.replace(tzinfo=tz, fold=0)
    if _roundtrip_matches(first, naive, tz):
        return first

    for minutes in range(1, 181):
        shifted_naive = naive + timedelta(minutes=minutes)
        shifted = shifted_naive.replace(tzinfo=tz, fold=0)
        if _roundtrip_matches(shifted, shifted_naive, tz):
            return shifted
    raise ValueError("Could not resolve local event time across the DST transition.")


def occurrence_times(
    first_start_at: int,
    recurrence: Recurrence,
    *,
    limit: int = 32,
) -> list[int]:
    """Return occurrence timestamps including the supplied first occurrence.

    The recurrence is evaluated in its IANA timezone and keeps the original local
    wall-clock time. The limit argument is a materialization bound, not the
    series end.
    """
    spec = recurrence.validate()
    if limit < 1:
        return []

    tz = ZoneInfo(spec.timezone_name)
    first = datetime.fromtimestamp(int(first_start_at), tz)
    wall = time(first.hour, first.minute, first.second)
    cursor = first.date()
    results: list[int] = []

    while len(results) < limit:
        include = False
        if spec.frequency == "daily":
            include = True
        elif spec.frequency == "weekly":
            include = cursor.weekday() == first.weekday()
        else:
            include = cursor.weekday() in spec.weekdays

        if include:
            resolved = resolve_local_wall_time(cursor, wall, spec.timezone_name)
            stamp = int(resolved.timestamp())
            if stamp >= int(first_start_at):
                if spec.until_at is not None and stamp > spec.until_at:
                    break
                results.append(stamp)
                if spec.occurrence_limit is not None and len(results) >= spec.occurrence_limit:
                    break

        cursor += timedelta(days=1)
        if (cursor - first.date()).days > 3660:
            raise ValueError("Recurring event horizon is too large.")

    return results


def recurrence_summary(recurrence: Recurrence) -> str:
    spec = recurrence.validate()
    if spec.frequency == "daily":
        base = "Daily"
    elif spec.frequency == "weekly":
        base = "Weekly"
    else:
        labels = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        base = "Every " + ", ".join(labels[day] for day in spec.weekdays)

    end = ""
    if spec.occurrence_limit is not None:
        end = f" · {spec.occurrence_limit} occurrence{'s' if spec.occurrence_limit != 1 else ''}"
    elif spec.until_at is not None:
        end_dt = datetime.fromtimestamp(spec.until_at, ZoneInfo(spec.timezone_name))
        end = f" · until {end_dt.date().isoformat()}"
    return f"{base} · {spec.timezone_name}{end}"
