"""SQLite-backed scheduler store for the portable Skill Runtime."""
from __future__ import annotations

import json
import time
import uuid
from types import MappingProxyType

from database import db
from skill_runtime.contracts.schedule import schedule_from_dict, schedule_to_dict
from skill_runtime.runtime.scheduler import ScheduledJob
from .skill_host import _valid_identity

_MAX_JOB_JSON_BYTES = 64 * 1024


def _encode_json(value, *, label: str) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable.") from exc
    if len(encoded.encode("utf-8")) > _MAX_JOB_JSON_BYTES:
        raise ValueError(f"{label} exceeds the 64 KiB host limit.")
    return encoded


def _decode_row(row) -> ScheduledJob:
    try:
        schedule = schedule_from_dict(json.loads(row["schedule_json"]))
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Persisted scheduler job is invalid.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Persisted scheduler payload must be an object.")
    return ScheduledJob(
        guild_id=int(row["guild_id"]),
        skill_id=row["skill_id"],
        key=row["job_key"],
        handler_id=row["handler_id"],
        schedule=schedule,
        payload=MappingProxyType(payload),
        next_run_at=int(row["next_run_at"]),
        last_run_at=int(row["last_run_at"]) if row["last_run_at"] is not None else None,
        failure_count=int(row["failure_count"]),
        revision=int(row["revision"]),
        claim_token=row["lease_token"],
    )


class GamerHQSchedulerStore:
    """Persistent scheduler state with atomic leases and revision-safe completion."""

    async def upsert_job(self, job: ScheduledJob) -> None:
        _valid_identity(job.guild_id, job.skill_id)
        schedule_json = _encode_json(schedule_to_dict(job.schedule), label="Schedule")
        payload_json = _encode_json(dict(job.payload), label="Scheduler payload")
        now = int(time.time())
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_jobs(
                    guild_id,skill_id,job_key,handler_id,schedule_json,payload_json,
                    enabled,next_run_at,last_run_at,failure_count,revision,last_error_code,
                    lease_until,lease_token,updated_at
                )
                VALUES(?,?,?,?,?,?,1,?,?,?,1,NULL,0,NULL,?)
                ON CONFLICT(guild_id,skill_id,job_key) DO UPDATE SET
                    handler_id=excluded.handler_id,
                    schedule_json=excluded.schedule_json,
                    payload_json=excluded.payload_json,
                    enabled=1,
                    next_run_at=excluded.next_run_at,
                    revision=skill_jobs.revision+1,
                    last_error_code=NULL,
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
        _valid_identity(guild_id, skill_id)
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM skill_jobs WHERE guild_id=? AND skill_id=? AND job_key=?",
                (guild_id, skill_id, key),
            )
            return cursor.rowcount == 1

    async def claim_due(self, *, now: int, lease_seconds: int, limit: int) -> tuple[ScheduledJob, ...]:
        if lease_seconds < 1 or limit < 1:
            raise ValueError("lease_seconds and limit must be positive.")
        claimed = []
        lease_until = int(now) + int(lease_seconds)
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
                (int(now), int(now), int(limit)),
            ).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                cursor = conn.execute(
                    """
                    UPDATE skill_jobs
                    SET lease_until=?,lease_token=?,updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND enabled=1 AND next_run_at IS NOT NULL
                      AND next_run_at<=? AND lease_until<=?
                    """,
                    (
                        lease_until,
                        token,
                        int(now),
                        row["guild_id"],
                        row["skill_id"],
                        row["job_key"],
                        row["revision"],
                        int(now),
                        int(now),
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                updated = dict(row)
                updated["lease_until"] = lease_until
                updated["lease_token"] = token
                try:
                    claimed.append(_decode_row(updated))
                except ValueError:
                    conn.execute(
                        """
                        UPDATE skill_jobs
                        SET enabled=0,last_error_code='invalid_persisted_job',
                            lease_until=0,lease_token=NULL,updated_at=?
                        WHERE guild_id=? AND skill_id=? AND job_key=?
                          AND revision=? AND lease_token=?
                        """,
                        (
                            int(now),
                            row["guild_id"],
                            row["skill_id"],
                            row["job_key"],
                            row["revision"],
                            token,
                        ),
                    )
        return tuple(claimed)

    def _owned_where(self, job: ScheduledJob) -> tuple:
        if not job.claim_token:
            raise ValueError("Claimed scheduler job requires a claim token.")
        return (
            job.guild_id,
            job.skill_id,
            job.key,
            job.revision,
            job.claim_token,
        )

    async def finish_success(self, job: ScheduledJob, *, ran_at: int, next_run_at: int | None) -> None:
        where = self._owned_where(job)
        with db.connect() as conn:
            if next_run_at is None:
                cursor = conn.execute(
                    """
                    DELETE FROM skill_jobs
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND lease_token=?
                    """,
                    where,
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE skill_jobs
                    SET next_run_at=?,last_run_at=?,failure_count=0,last_error_code=NULL,
                        lease_until=0,lease_token=NULL,updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND revision=? AND lease_token=?
                    """,
                    (int(next_run_at), int(ran_at), int(ran_at), *where),
                )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler claim is stale; completion was not applied.")

    async def finish_failure(self, job: ScheduledJob, *, error_code: str, next_run_at: int | None) -> None:
        where = self._owned_where(job)
        error_code = (error_code or "job_failed")[:80]
        now = int(time.time())
        with db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE skill_jobs
                SET next_run_at=?,failure_count=failure_count+1,last_error_code=?,
                    lease_until=0,lease_token=NULL,updated_at=?
                WHERE guild_id=? AND skill_id=? AND job_key=?
                  AND revision=? AND lease_token=?
                """,
                (next_run_at, error_code, now, *where),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler claim is stale; failure was not applied.")

    async def defer_job(self, job: ScheduledJob, *, next_run_at: int | None) -> None:
        where = self._owned_where(job)
        now = int(time.time())
        with db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE skill_jobs
                SET next_run_at=?,lease_until=0,lease_token=NULL,updated_at=?
                WHERE guild_id=? AND skill_id=? AND job_key=?
                  AND revision=? AND lease_token=?
                """,
                (next_run_at, now, *where),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler claim is stale; defer was not applied.")

    async def list_jobs(self, *, guild_id: int, skill_id: str | None = None) -> tuple[ScheduledJob, ...]:
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        with db.connect() as conn:
            if skill_id is None:
                rows = conn.execute(
                    "SELECT * FROM skill_jobs WHERE guild_id=? ORDER BY skill_id,job_key",
                    (guild_id,),
                ).fetchall()
            else:
                _valid_identity(guild_id, skill_id)
                rows = conn.execute(
                    "SELECT * FROM skill_jobs WHERE guild_id=? AND skill_id=? ORDER BY job_key",
                    (guild_id, skill_id),
                ).fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(_decode_row(row))
            except ValueError:
                continue
        return tuple(jobs)
