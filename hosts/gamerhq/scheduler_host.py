"""SQLite persistence adapter for the portable Skill scheduler."""
from __future__ import annotations

import json
import time
from types import MappingProxyType
from typing import Any

from database import db
from skill_runtime.contracts.schedule import schedule_from_dict, schedule_to_dict
from skill_runtime.runtime.scheduler import ScheduledJob


def _json(value: Any, *, label: str, max_bytes: int = 64 * 1024) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable.") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds the 64 KiB host limit.")
    return encoded


def _row_to_job(row) -> ScheduledJob:
    try:
        schedule = schedule_from_dict(json.loads(row["schedule_json"]))
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Stored scheduler job configuration is invalid.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Stored scheduler job payload must be an object.")
    if row["next_run_at"] is None:
        raise ValueError("Enabled scheduler job has no next execution.")
    return ScheduledJob(
        guild_id=int(row["guild_id"]),
        skill_id=row["skill_id"],
        key=row["job_key"],
        handler_id=row["handler_id"],
        schedule=schedule,
        payload=MappingProxyType(payload),
        next_run_at=int(row["next_run_at"]),
        last_run_at=int(row["last_run_at"]) if row["last_run_at"] is not None else None,
        failure_count=int(row["failure_count"] or 0),
    )


class GamerHQSkillJobStore:
    """Persistent scheduler store using GamerHQ's existing SQLite connection."""

    async def upsert_job(self, job: ScheduledJob) -> None:
        schedule_json = _json(schedule_to_dict(job.schedule), label="Schedule")
        payload_json = _json(dict(job.payload), label="Job payload")
        now = int(time.time())
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_jobs(
                    guild_id,skill_id,job_key,handler_id,schedule_json,payload_json,
                    enabled,next_run_at,last_run_at,failure_count,last_error_code,
                    lease_until,updated_at
                )
                VALUES(?,?,?,?,?,?,1,?,?,0,NULL,0,?)
                ON CONFLICT(guild_id,skill_id,job_key) DO UPDATE SET
                    handler_id=excluded.handler_id,
                    schedule_json=excluded.schedule_json,
                    payload_json=excluded.payload_json,
                    enabled=1,
                    next_run_at=excluded.next_run_at,
                    lease_until=0,
                    updated_at=excluded.updated_at
                """,
                (
                    job.guild_id,
                    job.skill_id,
                    job.key,
                    job.handler_id,
                    schedule_json,
                    payload_json,
                    job.next_run_at,
                    job.last_run_at,
                    now,
                ),
            )

    async def remove_job(self, *, guild_id: int, skill_id: str, key: str) -> bool:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM skill_jobs WHERE guild_id=? AND skill_id=? AND job_key=?",
                (guild_id, skill_id, key),
            )
            return cursor.rowcount == 1

    async def claim_due(self, *, now: int, lease_seconds: int, limit: int) -> tuple[ScheduledJob, ...]:
        """Atomically reserve due jobs before execution.

        GamerHQ currently supports one active process per live guild, but the
        lease also protects against overlapping scheduler ticks and crash/restart
        windows.
        """
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM skill_jobs
                WHERE enabled=1
                  AND next_run_at IS NOT NULL
                  AND next_run_at<=?
                  AND lease_until<=?
                ORDER BY next_run_at,guild_id,skill_id,job_key
                LIMIT ?
                """,
                (now, now, limit),
            ).fetchall()
            lease_until = now + lease_seconds
            for row in rows:
                conn.execute(
                    """
                    UPDATE skill_jobs
                    SET lease_until=?,updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND enabled=1 AND next_run_at=? AND lease_until<=?
                    """,
                    (
                        lease_until,
                        now,
                        row["guild_id"],
                        row["skill_id"],
                        row["job_key"],
                        row["next_run_at"],
                        now,
                    ),
                )
            # Re-read only successfully leased rows. This keeps the adapter safe
            # if a second connection won a race before the write lock.
            claimed = []
            for row in rows:
                current = conn.execute(
                    """
                    SELECT * FROM skill_jobs
                    WHERE guild_id=? AND skill_id=? AND job_key=? AND lease_until=?
                    """,
                    (row["guild_id"], row["skill_id"], row["job_key"], lease_until),
                ).fetchone()
                if current is None:
                    continue
                try:
                    claimed.append(_row_to_job(current))
                except ValueError:
                    # A malformed persisted job must not crash the scheduler
                    # loop or leak its stored values. Disable it for owner
                    # diagnostics; explicit reconfiguration can re-enable it.
                    conn.execute(
                        """
                        UPDATE skill_jobs
                        SET enabled=0,next_run_at=NULL,last_error_code='invalid_configuration',
                            lease_until=0,updated_at=?
                        WHERE guild_id=? AND skill_id=? AND job_key=?
                        """,
                        (now, row["guild_id"], row["skill_id"], row["job_key"]),
                    )
        return tuple(claimed)

    async def finish_success(
        self,
        job: ScheduledJob,
        *,
        ran_at: int,
        next_run_at: int | None,
    ) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE skill_jobs
                SET enabled=?,next_run_at=?,last_run_at=?,failure_count=0,
                    last_error_code=NULL,lease_until=0,updated_at=?
                WHERE guild_id=? AND skill_id=? AND job_key=?
                """,
                (
                    int(next_run_at is not None),
                    next_run_at,
                    ran_at,
                    int(time.time()),
                    job.guild_id,
                    job.skill_id,
                    job.key,
                ),
            )

    async def finish_failure(
        self,
        job: ScheduledJob,
        *,
        error_code: str,
        next_run_at: int | None,
    ) -> None:
        safe_code = str(error_code)[:80] or "job_failed"
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE skill_jobs
                SET enabled=?,next_run_at=?,failure_count=failure_count+1,
                    last_error_code=?,lease_until=0,updated_at=?
                WHERE guild_id=? AND skill_id=? AND job_key=?
                """,
                (
                    int(next_run_at is not None),
                    next_run_at,
                    safe_code,
                    int(time.time()),
                    job.guild_id,
                    job.skill_id,
                    job.key,
                ),
            )

    async def defer_job(self, job: ScheduledJob, *, next_run_at: int | None) -> None:
        """Advance a disabled Skill's job without counting it as a run/failure."""
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE skill_jobs
                SET enabled=?,next_run_at=?,lease_until=0,updated_at=?
                WHERE guild_id=? AND skill_id=? AND job_key=?
                """,
                (
                    int(next_run_at is not None),
                    next_run_at,
                    int(time.time()),
                    job.guild_id,
                    job.skill_id,
                    job.key,
                ),
            )

    async def jobs_for_skill(self, *, guild_id: int, skill_id: str) -> tuple[dict, ...]:
        """Host diagnostics only; not exposed through the portable SkillContext."""
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT job_key,handler_id,enabled,next_run_at,last_run_at,
                       failure_count,last_error_code
                FROM skill_jobs
                WHERE guild_id=? AND skill_id=?
                ORDER BY job_key
                """,
                (guild_id, skill_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)
