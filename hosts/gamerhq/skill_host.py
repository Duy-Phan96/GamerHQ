"""GamerHQ adapters for portable Skill Runtime contracts.

This module may import GamerHQ infrastructure. skill_runtime/ must never import
this module or any other GamerHQ host implementation.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Iterable, Mapping

from database import db
from skill_runtime.contracts.capabilities import KNOWN_CAPABILITIES, SkillCapability
from skill_runtime.contracts.manifest import SKILL_ID
from skill_runtime.contracts.schedule import schedule_from_dict, schedule_to_dict
from skill_runtime.runtime.scheduler import ScheduledJob

_STORAGE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_STORAGE_JSON_BYTES = 64 * 1024


def _valid_identity(guild_id: int, skill_id: str) -> None:
    if guild_id <= 0:
        raise ValueError("guild_id must be positive.")
    if not SKILL_ID.fullmatch(skill_id):
        raise ValueError("skill_id must be a valid stable Skill ID.")


class GamerHQSkillStateStore:
    """SQLite-backed per-guild enablement state owned by the GamerHQ host."""

    async def is_enabled(self, *, guild_id: int, skill_id: str) -> bool:
        _valid_identity(guild_id, skill_id)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM skill_guild_state WHERE guild_id=? AND skill_id=?",
                (guild_id, skill_id),
            ).fetchone()
        return bool(row and row["enabled"])

    async def set_enabled(self, *, guild_id: int, skill_id: str, enabled: bool, version: str) -> None:
        _valid_identity(guild_id, skill_id)
        if not version:
            raise ValueError("Skill version is required.")
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_guild_state(guild_id,skill_id,enabled,version,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id,skill_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (guild_id, skill_id, int(bool(enabled)), version, int(time.time())),
            )

    async def enabled_skill_ids(self, *, guild_id: int) -> tuple[str, ...]:
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT skill_id FROM skill_guild_state WHERE guild_id=? AND enabled=1 ORDER BY skill_id",
                (guild_id,),
            ).fetchall()
        return tuple(row["skill_id"] for row in rows)


class CapabilityPermissions:
    """Runtime capability gate built from a Skill's declared manifest permissions."""

    def __init__(self, declared: Iterable[str], *, available: Iterable[str] = KNOWN_CAPABILITIES):
        self._declared = frozenset(str(value) for value in declared)
        self._available = frozenset(str(value) for value in available)
        unknown = self._declared - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError("Unknown declared Skill capability: " + ", ".join(sorted(unknown)))

    def allows(self, capability: str) -> bool:
        return capability in self._declared and capability in self._available

    def require(self, capability: str) -> None:
        if capability not in KNOWN_CAPABILITIES:
            raise PermissionError("Unknown Skill capability.")
        if capability not in self._declared:
            raise PermissionError(f"Skill did not declare capability: {capability}.")
        if capability not in self._available:
            raise PermissionError(f"Host does not provide capability: {capability}.")


class GamerHQSkillStorage:
    """Namespaced JSON storage visible to exactly one guild + Skill pair."""

    def __init__(self, *, guild_id: int, skill_id: str, permissions: CapabilityPermissions):
        _valid_identity(guild_id, skill_id)
        self.guild_id = guild_id
        self.skill_id = skill_id
        self.permissions = permissions

    def _key(self, key: str) -> str:
        if not _STORAGE_KEY.fullmatch(str(key)):
            raise ValueError("Skill storage key must be 1-128 safe characters.")
        return str(key)

    async def get(self, key: str) -> Any | None:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        key = self._key(key)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM skill_storage WHERE guild_id=? AND skill_id=? AND storage_key=?",
                (self.guild_id, self.skill_id, key),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value_json"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Stored Skill value is invalid JSON.") from exc

    async def set(self, key: str, value: Any) -> None:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        key = self._key(key)
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Skill storage values must be JSON serializable.") from exc
        if len(encoded.encode("utf-8")) > _MAX_STORAGE_JSON_BYTES:
            raise ValueError("Skill storage value exceeds the 64 KiB host limit.")
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_storage(guild_id,skill_id,storage_key,value_json,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id,skill_id,storage_key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (self.guild_id, self.skill_id, key, encoded, int(time.time())),
            )

    async def delete(self, key: str) -> None:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        key = self._key(key)
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM skill_storage WHERE guild_id=? AND skill_id=? AND storage_key=?",
                (self.guild_id, self.skill_id, key),
            )


class GamerHQSkillAudit:
    """Adapter onto the existing private GamerHQ server log; no competing audit UI."""

    def __init__(self, *, guild, skill_id: str, permissions: CapabilityPermissions):
        _valid_identity(guild.id, skill_id)
        self.guild = guild
        self.skill_id = skill_id
        self.permissions = permissions
        self.log = logging.getLogger(f"gamerhq.skill.{skill_id}")

    async def write(
        self,
        *,
        action: str,
        target: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.permissions.require(SkillCapability.AUDIT_WRITE.value)
        if not action or len(action) > 100:
            raise ValueError("Skill audit action must be 1-100 characters.")
        # Metadata values may contain private Skill data. The shared operational
        # log gets only bounded keys; richer structured Skill audit can be added
        # later by extending the existing audit infrastructure, not by leaking it.
        keys = ", ".join(sorted(str(key)[:40] for key in (metadata or {})))[:300]
        detail = f"Skill **{self.skill_id}** · action **{action}**"
        if target:
            detail += f"\nTarget: {str(target)[:160]}"
        if keys:
            detail += f"\nMetadata fields: {keys}"
        from services.server_log_service import emit
        sent = await emit(
            self.guild,
            "skill:" + uuid.uuid4().hex,
            "🧩 Skill Activity",
            detail,
        )
        if not sent:
            self.log.info("skill_audit action=%s result=server_log_unavailable", action)


class GamerHQSchedulerStore:
    """SQLite persistence/claim adapter for the portable SchedulerEngine.

    Claims use BEGIN IMMEDIATE plus a lease token and revision so two scheduler
    workers cannot both own the same due execution, and a stale worker cannot
    overwrite a job that was edited while it was running.
    """

    @staticmethod
    def _job(row) -> ScheduledJob:
        try:
            schedule = schedule_from_dict(json.loads(row["schedule_json"]))
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("Stored Skill scheduler job is malformed.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Stored Skill scheduler payload must be an object.")
        return ScheduledJob(
            guild_id=int(row["guild_id"]),
            skill_id=row["skill_id"],
            key=row["job_key"],
            handler_id=row["handler_id"],
            schedule=schedule,
            payload=payload,
            next_run_at=int(row["next_run_at"]),
            last_run_at=int(row["last_run_at"]) if row["last_run_at"] is not None else None,
            failure_count=int(row["failure_count"]),
            revision=int(row["revision"]),
            claim_token=row["lease_token"],
        )

    async def upsert_job(self, job: ScheduledJob) -> None:
        _valid_identity(job.guild_id, job.skill_id)
        schedule_json = json.dumps(schedule_to_dict(job.schedule), separators=(",", ":"), sort_keys=True)
        payload_json = json.dumps(dict(job.payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(payload_json.encode("utf-8")) > _MAX_STORAGE_JSON_BYTES:
            raise ValueError("Scheduler payload exceeds the 64 KiB host limit.")
        now = int(time.time())
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_jobs(
                    guild_id,skill_id,job_key,handler_id,schedule_json,payload_json,
                    enabled,next_run_at,last_run_at,failure_count,revision,last_error_code,
                    lease_until,lease_token,updated_at
                )
                VALUES(?,?,?,?,?,?,1,?,NULL,0,1,NULL,0,NULL,?)
                ON CONFLICT(guild_id,skill_id,job_key) DO UPDATE SET
                    handler_id=excluded.handler_id,
                    schedule_json=excluded.schedule_json,
                    payload_json=excluded.payload_json,
                    enabled=1,
                    next_run_at=excluded.next_run_at,
                    failure_count=0,
                    revision=skill_jobs.revision+1,
                    last_error_code=NULL,
                    lease_until=0,
                    lease_token=NULL,
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
        if now < 0 or lease_seconds <= 0 or limit <= 0:
            raise ValueError("Invalid scheduler claim arguments.")
        token = uuid.uuid4().hex
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
            if not rows:
                return ()
            identities = [
                (int(row["guild_id"]), row["skill_id"], row["job_key"], int(row["revision"]))
                for row in rows
            ]
            claimed = []
            for guild_id, skill_id, job_key, revision in identities:
                cursor = conn.execute(
                    """
                    UPDATE skill_jobs
                    SET lease_until=?, lease_token=?, updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=?
                      AND enabled=1 AND revision=? AND lease_until<=?
                    """,
                    (
                        now + lease_seconds,
                        token,
                        now,
                        guild_id,
                        skill_id,
                        job_key,
                        revision,
                        now,
                    ),
                )
                if cursor.rowcount == 1:
                    row = conn.execute(
                        "SELECT * FROM skill_jobs WHERE guild_id=? AND skill_id=? AND job_key=?",
                        (guild_id, skill_id, job_key),
                    ).fetchone()
                    claimed.append(self._job(row))
            return tuple(claimed)

    def _owned_where(self, job: ScheduledJob) -> tuple:
        if not job.claim_token:
            raise ValueError("Scheduler completion requires a claimed job.")
        return (job.guild_id, job.skill_id, job.key, job.revision, job.claim_token)

    async def finish_success(self, job: ScheduledJob, *, ran_at: int, next_run_at: int | None) -> None:
        where = self._owned_where(job)
        with db.connect() as conn:
            if next_run_at is None:
                cursor = conn.execute(
                    """
                    DELETE FROM skill_jobs
                    WHERE guild_id=? AND skill_id=? AND job_key=? AND revision=? AND lease_token=?
                    """,
                    where,
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE skill_jobs
                    SET next_run_at=?,last_run_at=?,failure_count=0,last_error_code=NULL,
                        lease_until=0,lease_token=NULL,updated_at=?
                    WHERE guild_id=? AND skill_id=? AND job_key=? AND revision=? AND lease_token=?
                    """,
                    (next_run_at, ran_at, ran_at, *where),
                )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler job changed while executing; stale completion rejected.")

    async def finish_failure(self, job: ScheduledJob, *, error_code: str, next_run_at: int | None) -> None:
        where = self._owned_where(job)
        now = int(time.time())
        with db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE skill_jobs
                SET next_run_at=?,failure_count=failure_count+1,last_error_code=?,
                    lease_until=0,lease_token=NULL,updated_at=?
                WHERE guild_id=? AND skill_id=? AND job_key=? AND revision=? AND lease_token=?
                """,
                (next_run_at, str(error_code)[:80], now, *where),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler job changed while executing; stale failure rejected.")

    async def defer_job(self, job: ScheduledJob, *, next_run_at: int | None) -> None:
        where = self._owned_where(job)
        now = int(time.time())
        with db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE skill_jobs
                SET next_run_at=?,lease_until=0,lease_token=NULL,updated_at=?
                WHERE guild_id=? AND skill_id=? AND job_key=? AND revision=? AND lease_token=?
                """,
                (next_run_at, now, *where),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Scheduler job changed while executing; stale defer rejected.")
