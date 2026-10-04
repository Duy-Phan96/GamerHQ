"""SQLite persistence adapter for the portable Skill scheduler."""
from __future__ import annotations

import json
import time
import uuid
from types import MappingProxyType

from database import db
from skill_runtime.contracts.schedule import schedule_from_dict, schedule_to_dict
from skill_runtime.runtime.scheduler import ScheduledJob


class GamerHQSchedulerStore:
    """Persistent Skill scheduler state owned by the GamerHQ host.

    Claim/finish operations use a lease + claim token + revision so parallel
    workers do not execute the same due row and stale workers cannot overwrite
    a job that was edited while they were running.
    """

    @staticmethod
    def _row_to_job(row) -> ScheduledJob:
        try:
            schedule = schedule_from_dict(json.loads(row["schedule_json"]))
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Stored Skill scheduler row is invalid.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Stored Skill scheduler payload must be an object.")
        return ScheduledJob(
            guild_id=int(row["guild_id"]),
            skill_id=row["skill_id"],
            key=row["job_key"],
            handler_id=row["handler_id"],
            schedule=schedule,
            payload=MappingProxyType(dict(payload)),
            next_run_at=int(row["next_run_at"]),
            last_run_at=int(row["last_run_at"]) if row["last_run_at"] is not None else None,
            failure_count=int(row["failure_count"]),
            revision=int(row["revision"]),
            claim_token=row["claim_token"],
        )

    async def upsert_job(self, job: ScheduledJob) -> None:
        schedule_json = json.dumps(
            schedule_to_dict(job.schedule),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload_json = json.dumps(
            dict(job.payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        now = int(time.time())
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_jobs(
                    guild_id,skill_id,job_key,handler_id,schedule_json,payload_json,
                    next_run_at,last_run_at,failure_count,last_error_code,
                    lease_until,claim_token,revision,updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,1,?)
                ON CONFLICT(guild_id,skill_id,job_key) DO UPDATE SET
                    handler_id=excluded.handler_id,
                    schedule_json=excluded.schedule_json,
                    payload_json=excluded.payload_json,
                    next_run_at=excluded.next_run_at,
                    last_error_code=NULL,
                    lease_until=NULL,
                    claim_token=NULL,
                    revision=skill_jobs.revision+1,
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
                    job.failure_count,
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

    async def claim_due(
        self,
        *,
        now: int,
        lease_seconds: int,
        limit: int,
    ) -> tuple[ScheduledJob, ...]:
        lease_until = int(now) + int(lease_seconds)
        claimed = []
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM skill_jobs
                WHERE next_run_at<=?
                  AND (lease_until IS NULL OR lease_until<=?)
                ORDER BY next_run_at,guild_id,skill_id,job_key
                LIMIT ?
                """,
                (now, now, limit),
            ).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                cursor = conn.execute(
                    """
                    UPDATE skill_jobs
                    SET lease_until=?, claim_token=?, updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=?
                      AND (lease_until IS NULL OR lease_until<=?)
                    """,
                    (
                        lease_until,
                        token,
                        now,
                        row["guild_id"],
                        row["skill_id"],
                        row["job_key"],
                        row["revision"],
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                current = dict(row)
                current["lease_until"] = lease_until
                current["claim_token"] = token
                claimed.append(self._row_to_job(current))
        return tuple(claimed)

    def _claim_where(self, job: ScheduledJob) -> tuple:
        if not job.claim_token:
            raise ValueError("Scheduler job is not claimed.")
        return (
            job.guild_id,
            job.skill_id,
            job.key,
            job.revision,
            job.claim_token,
        )

    async def finish_success(
        self,
        job: ScheduledJob,
        *,
        ran_at: int,
        next_run_at: int | None,
    ) -> None:
        where = self._claim_where(job)
        with db.connect() as conn:
            if next_run_at is None:
                cursor = conn.execute(
                    """
                    DELETE FROM skill_jobs
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND claim_token=?
                    """,
                    where,
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE skill_jobs
                    SET next_run_at=?, last_run_at=?, failure_count=0,
                        last_error_code=NULL, lease_until=NULL, claim_token=NULL,
                        updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND claim_token=?
                    """,
                    (next_run_at, ran_at, ran_at, *where),
                )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler claim became stale before success was recorded.")

    async def finish_failure(
        self,
        job: ScheduledJob,
        *,
        error_code: str,
        next_run_at: int | None,
    ) -> None:
        where = self._claim_where(job)
        if not error_code:
            error_code = "job_failed"
        with db.connect() as conn:
            if next_run_at is None:
                cursor = conn.execute(
                    """
                    DELETE FROM skill_jobs
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND claim_token=?
                    """,
                    where,
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE skill_jobs
                    SET next_run_at=?, failure_count=failure_count+1,
                        last_error_code=?, lease_until=NULL, claim_token=NULL,
                        updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND claim_token=?
                    """,
                    (next_run_at, error_code[:80], int(time.time()), *where),
                )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler claim became stale before failure was recorded.")

    async def defer_job(self, job: ScheduledJob, *, next_run_at: int | None) -> None:
        where = self._claim_where(job)
        with db.connect() as conn:
            if next_run_at is None:
                cursor = conn.execute(
                    """
                    DELETE FROM skill_jobs
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND claim_token=?
                    """,
                    where,
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE skill_jobs
                    SET next_run_at=?, lease_until=NULL, claim_token=NULL,
                        updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND claim_token=?
                    """,
                    (next_run_at, int(time.time()), *where),
                )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler claim became stale before defer was recorded.")
