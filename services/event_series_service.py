"""Recurring Event Manager occurrence materialization.

Turns an Event Series into ordinary lfg_events rows. Concrete occurrences remain
owned by the existing event lifecycle, while this module only decides which
series instances should exist.
"""
from __future__ import annotations

import secrets
import sqlite3
import time

from database import db
from services.event_recurrence_service import Recurrence, occurrence_times


DEFAULT_HORIZON_DAYS = 28
MAX_PLANNED_OCCURRENCES = 365


def recurrence_from_series(series: dict) -> Recurrence:
    return Recurrence(
        frequency=str(series["frequency"]),
        timezone_name=str(series["timezone_name"]),
        weekdays=tuple(series.get("weekdays") or ()),
        occurrence_limit=series.get("occurrence_limit"),
        until_at=series.get("until_at"),
    ).validate()


def materialize_series(
    series: dict,
    *,
    now: int | None = None,
    through_at: int | None = None,
) -> list[dict]:
    """Create missing future occurrences for one active series.

    Existing series/occurrence-index rows are authoritative. Re-running this
    function is safe and does not duplicate occurrences.
    """
    if series.get("status") != "active":
        return []

    now = int(time.time()) if now is None else int(now)
    through_at = now + DEFAULT_HORIZON_DAYS * 86400 if through_at is None else int(through_at)
    if through_at <= now:
        return []

    planned = occurrence_times(
        int(series["first_start_at"]),
        recurrence_from_series(series),
        limit=MAX_PLANNED_OCCURRENCES,
    )
    existing = {
        int(row["occurrence_index"]): row
        for row in db.get_lfg_series_occurrences(int(series["id"]))
        if row.get("occurrence_index") is not None
    }

    created = []
    for index, start_at in enumerate(planned):
        if start_at > through_at:
            break
        if index in existing or start_at <= now:
            continue
        try:
            event = db.create_lfg_event(
                guild_id=int(series["guild_id"]),
                game_id=0,
                host_id=int(series["host_id"]),
                title=str(series["title"]),
                start_at=int(start_at),
                max_players=int(series["max_players"]),
                invite_lead_minutes=int(series["invite_lead_minutes"]),
                duration_minutes=int(series.get("duration_minutes") or 120),
                visibility=str(series.get("visibility") or "public"),
                share_token=secrets.token_urlsafe(8),
                series_id=int(series["id"]),
                occurrence_index=index,
            )
        except sqlite3.IntegrityError:
            # A concurrent/retried materializer won the unique occurrence claim.
            continue
        created.append(event)
    return created


def materialize_guild(
    guild_id: int,
    *,
    now: int | None = None,
    through_at: int | None = None,
) -> list[dict]:
    created = []
    for series in db.get_active_lfg_event_series(int(guild_id)):
        created.extend(materialize_series(series, now=now, through_at=through_at))
    return created
