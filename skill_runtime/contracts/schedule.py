from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIN_INTERVAL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class OnceSchedule:
    run_at: int

    def __post_init__(self) -> None:
        if self.run_at < 0:
            raise ValueError("run_at must be non-negative.")


@dataclass(frozen=True, slots=True)
class IntervalSchedule:
    seconds: int

    def __post_init__(self) -> None:
        if self.seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"Interval must be at least {MIN_INTERVAL_SECONDS} seconds.")


@dataclass(frozen=True, slots=True)
class DailySchedule:
    hour: int
    minute: int
    timezone: str

    def __post_init__(self) -> None:
        _validate_clock(self.hour, self.minute)
        _zone(self.timezone)


@dataclass(frozen=True, slots=True)
class WeeklySchedule:
    weekday: int
    hour: int
    minute: int
    timezone: str

    def __post_init__(self) -> None:
        if self.weekday not in range(7):
            raise ValueError("weekday must be 0 (Monday) through 6 (Sunday).")
        _validate_clock(self.hour, self.minute)
        _zone(self.timezone)


ScheduleSpec: TypeAlias = OnceSchedule | IntervalSchedule | DailySchedule | WeeklySchedule


def _validate_clock(hour: int, minute: int) -> None:
    if hour not in range(24) or minute not in range(60):
        raise ValueError("Schedule time must use a valid 24-hour clock.")


def _zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A timezone is required for fixed-time schedules.")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown IANA timezone.") from exc


def _local_candidate(day: datetime, *, hour: int, minute: int, zone: ZoneInfo) -> datetime | None:
    """Return one real local wall-clock occurrence.

    Non-existent DST wall times are skipped. Ambiguous times use fold=0 (the
    first occurrence) so one schedule never executes twice for the same local
    clock value.
    """
    naive = day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None)
    candidate = naive.replace(tzinfo=zone, fold=0)
    roundtrip = datetime.fromtimestamp(candidate.timestamp(), zone)
    if roundtrip.replace(tzinfo=None) != naive:
        return None
    return candidate


def next_run_at(schedule: ScheduleSpec, *, after: int) -> int | None:
    """Return the first execution strictly after the supplied epoch."""
    if after < 0:
        raise ValueError("after must be non-negative.")
    if isinstance(schedule, OnceSchedule):
        return schedule.run_at if schedule.run_at > after else None
    if isinstance(schedule, IntervalSchedule):
        return after + schedule.seconds

    zone = _zone(schedule.timezone)
    local_after = datetime.fromtimestamp(after, zone)

    if isinstance(schedule, DailySchedule):
        day = local_after
        for offset in range(0, 8):
            candidate = _local_candidate(day + timedelta(days=offset), hour=schedule.hour, minute=schedule.minute, zone=zone)
            if candidate is not None and int(candidate.timestamp()) > after:
                return int(candidate.timestamp())
        raise RuntimeError("Could not resolve daily schedule.")

    if isinstance(schedule, WeeklySchedule):
        days_until = (schedule.weekday - local_after.weekday()) % 7
        first = local_after + timedelta(days=days_until)
        for week in range(0, 3):
            candidate = _local_candidate(first + timedelta(days=7 * week), hour=schedule.hour, minute=schedule.minute, zone=zone)
            if candidate is not None and int(candidate.timestamp()) > after:
                return int(candidate.timestamp())
        raise RuntimeError("Could not resolve weekly schedule.")

    raise TypeError("Unsupported schedule type.")


def schedule_to_dict(schedule: ScheduleSpec) -> dict[str, Any]:
    if isinstance(schedule, OnceSchedule):
        return {"type": "once", "runAt": schedule.run_at}
    if isinstance(schedule, IntervalSchedule):
        return {"type": "interval", "seconds": schedule.seconds}
    if isinstance(schedule, DailySchedule):
        return {
            "type": "daily",
            "hour": schedule.hour,
            "minute": schedule.minute,
            "timezone": schedule.timezone,
        }
    if isinstance(schedule, WeeklySchedule):
        return {
            "type": "weekly",
            "weekday": schedule.weekday,
            "hour": schedule.hour,
            "minute": schedule.minute,
            "timezone": schedule.timezone,
        }
    raise TypeError("Unsupported schedule type.")


def schedule_from_dict(value: Mapping[str, Any]) -> ScheduleSpec:
    if not isinstance(value, Mapping):
        raise ValueError("Schedule must be an object.")
    kind = value.get("type")

    def integer(name: str) -> int:
        try:
            raw = value[name]
            if isinstance(raw, bool):
                raise TypeError
            return int(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Malformed schedule configuration.") from exc

    if kind == "once":
        return OnceSchedule(run_at=integer("runAt"))
    if kind == "interval":
        return IntervalSchedule(seconds=integer("seconds"))
    if kind == "daily":
        if "timezone" not in value:
            raise ValueError("Malformed schedule configuration.")
        return DailySchedule(
            hour=integer("hour"),
            minute=integer("minute"),
            timezone=str(value["timezone"]),
        )
    if kind == "weekly":
        if "timezone" not in value:
            raise ValueError("Malformed schedule configuration.")
        return WeeklySchedule(
            weekday=integer("weekday"),
            hour=integer("hour"),
            minute=integer("minute"),
            timezone=str(value["timezone"]),
        )
    raise ValueError("Unknown schedule type.")
