from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from ..contracts.capabilities import SkillCapability
from ..contracts.schedule import ScheduleSpec, next_run_at, schedule_from_dict, schedule_to_dict
from .registry import SkillRegistry

JOB_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HANDLER_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[a-z][a-z0-9-]*)*\.v[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    guild_id: int
    skill_id: str
    key: str
    handler_id: str
    schedule: ScheduleSpec
    payload: Mapping[str, Any]
    next_run_at: int
    last_run_at: int | None = None
    failure_count: int = 0
    revision: int = 1
    claim_token: str | None = None


@dataclass(frozen=True, slots=True)
class SchedulerRunReport:
    claimed: int
    executed: int
    failed: int
    deferred_disabled: int
    unavailable_handlers: int


class SchedulerStorePort(Protocol):
    async def upsert_job(self, job: ScheduledJob) -> None: ...
    async def remove_job(self, *, guild_id: int, skill_id: str, key: str) -> bool: ...
    async def claim_due(self, *, now: int, lease_seconds: int, limit: int) -> tuple[ScheduledJob, ...]: ...
    async def finish_success(self, job: ScheduledJob, *, ran_at: int, next_run_at: int | None) -> None: ...
    async def finish_failure(self, job: ScheduledJob, *, error_code: str, next_run_at: int | None) -> None: ...
    async def defer_job(self, job: ScheduledJob, *, next_run_at: int | None) -> None: ...


JobHandler = Callable[[ScheduledJob], Awaitable[None]]
AvailabilityCheck = Callable[..., Awaitable[bool]]


class SchedulerEngine:
    """Portable persistent scheduler coordinator.

    Persistence and host lifecycle availability are provided through ports. The
    engine never imports GamerHQ, Discord.py or a concrete database.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        store: SchedulerStorePort,
        *,
        availability: AvailabilityCheck | None = None,
        lease_seconds: int = 120,
    ):
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30.")
        self.registry = registry
        self.store = store
        self.availability = availability
        self.lease_seconds = lease_seconds
        self._handlers: dict[tuple[str, str], JobHandler] = {}

    def _require_scheduler_capability(self, skill_id: str) -> None:
        skill = self.registry.get(skill_id)
        if SkillCapability.SCHEDULER_JOBS.value not in skill.manifest.permissions:
            raise PermissionError(
                f"Skill {skill_id} did not declare capability: {SkillCapability.SCHEDULER_JOBS.value}."
            )

    def register_handler(self, *, skill_id: str, handler_id: str, handler: JobHandler) -> None:
        self._require_scheduler_capability(skill_id)
        if not HANDLER_ID.fullmatch(handler_id):
            raise ValueError("Scheduler handler IDs must be stable and versioned.")
        key = (skill_id, handler_id)
        if key in self._handlers:
            raise ValueError(f"Scheduler handler already registered: {skill_id}/{handler_id}.")
        self._handlers[key] = handler

    def unregister_skill(self, *, skill_id: str) -> int:
        keys = [key for key in self._handlers if key[0] == skill_id]
        for key in keys:
            self._handlers.pop(key, None)
        return len(keys)

    async def upsert_job(
        self,
        *,
        guild_id: int,
        skill_id: str,
        key: str,
        handler_id: str,
        schedule: ScheduleSpec,
        payload: Mapping[str, Any],
        now: int | None = None,
    ) -> ScheduledJob:
        self._require_scheduler_capability(skill_id)
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        if not JOB_KEY.fullmatch(key):
            raise ValueError("Scheduler job key must be 1-128 safe characters.")
        if not HANDLER_ID.fullmatch(handler_id):
            raise ValueError("Scheduler handler IDs must be stable and versioned.")
        if (skill_id, handler_id) not in self._handlers:
            raise ValueError("Scheduler handler must be registered before scheduling jobs.")
        now = int(time.time()) if now is None else int(now)
        next_run = next_run_at(schedule, after=now)
        if next_run is None:
            raise ValueError("Schedule has no future execution.")
        job = ScheduledJob(
            guild_id=guild_id,
            skill_id=skill_id,
            key=key,
            handler_id=handler_id,
            schedule=schedule,
            payload=MappingProxyType(dict(payload)),
            next_run_at=next_run,
        )
        await self.store.upsert_job(job)
        return job

    async def remove_job(self, *, guild_id: int, skill_id: str, key: str) -> bool:
        self._require_scheduler_capability(skill_id)
        return await self.store.remove_job(guild_id=guild_id, skill_id=skill_id, key=key)

    async def run_due(self, *, now: int | None = None, limit: int = 50) -> SchedulerRunReport:
        now = int(time.time()) if now is None else int(now)
        if limit not in range(1, 501):
            raise ValueError("limit must be between 1 and 500.")
        jobs = await self.store.claim_due(now=now, lease_seconds=self.lease_seconds, limit=limit)
        executed = failed = deferred = unavailable = 0

        for job in jobs:
            next_run = next_run_at(job.schedule, after=now)
            if self.availability is not None and not await self.availability(
                guild_id=job.guild_id,
                skill_id=job.skill_id,
            ):
                # Recurring jobs skip missed executions while disabled. A due
                # one-shot has no later recurrence, so retain it with a short
                # retry window instead of silently losing it.
                deferred_next = next_run if next_run is not None else now + 60
                await self.store.defer_job(job, next_run_at=deferred_next)
                deferred += 1
                continue

            handler = self._handlers.get((job.skill_id, job.handler_id))
            if handler is None:
                await self.store.finish_failure(
                    job,
                    error_code="handler_unavailable",
                    next_run_at=next_run if next_run is not None else now + 60,
                )
                unavailable += 1
                failed += 1
                continue

            try:
                await handler(job)
            except asyncio.CancelledError:
                # Let process shutdown release the lease naturally. Another
                # scheduler instance can retry after lease expiry.
                raise
            except Exception as exc:
                await self.store.finish_failure(
                    job,
                    error_code=type(exc).__name__[:80] or "job_failed",
                    next_run_at=next_run if next_run is not None else now + 60,
                )
                failed += 1
                continue

            await self.store.finish_success(job, ran_at=now, next_run_at=next_run)
            executed += 1

        return SchedulerRunReport(
            claimed=len(jobs),
            executed=executed,
            failed=failed,
            deferred_disabled=deferred,
            unavailable_handlers=unavailable,
        )

    async def serve(
        self,
        stop: asyncio.Event,
        *,
        poll_seconds: float = 15.0,
        limit: int = 50,
    ) -> None:
        if poll_seconds < 1:
            raise ValueError("poll_seconds must be at least 1.")
        while not stop.is_set():
            await self.run_due(limit=limit)
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass


class ScopedScheduler:
    """SkillContext-facing scheduler bound to one guild and Skill identity."""

    def __init__(self, engine: SchedulerEngine, *, guild_id: int, skill_id: str):
        self.engine = engine
        self.guild_id = guild_id
        self.skill_id = skill_id

    async def upsert_job(
        self,
        *,
        key: str,
        handler_id: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        await self.engine.upsert_job(
            guild_id=self.guild_id,
            skill_id=self.skill_id,
            key=key,
            handler_id=handler_id,
            schedule=schedule_from_dict(schedule),
            payload=payload,
        )

    async def remove_job(self, *, key: str) -> None:
        await self.engine.remove_job(
            guild_id=self.guild_id,
            skill_id=self.skill_id,
            key=key,
        )

    @staticmethod
    def serialize_schedule(schedule: ScheduleSpec) -> dict[str, Any]:
        return schedule_to_dict(schedule)
