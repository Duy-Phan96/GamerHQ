import asyncio
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.manifest import SkillManifest
from skill_runtime.contracts.schedule import (
    DailySchedule,
    IntervalSchedule,
    OnceSchedule,
    WeeklySchedule,
    next_run_at,
    schedule_from_dict,
    schedule_to_dict,
)
from skill_runtime.runtime.registry import SkillRegistry
from skill_runtime.runtime.scheduler import ScheduledJob, SchedulerEngine


class FakeSkill:
    def __init__(self, skill_id="scheduler-skill", *, scheduler=True):
        permissions = (SkillCapability.SCHEDULER_JOBS.value,) if scheduler else ()
        self.manifest = SkillManifest(
            id=skill_id,
            name=skill_id.title(),
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=permissions,
        )


class FakeStore:
    def __init__(self):
        self.jobs = {}
        self.claimed = []
        self.successes = []
        self.failures = []
        self.deferred = []

    async def upsert_job(self, job):
        self.jobs[(job.guild_id, job.skill_id, job.key)] = job

    async def remove_job(self, *, guild_id, skill_id, key):
        return self.jobs.pop((guild_id, skill_id, key), None) is not None

    async def claim_due(self, *, now, lease_seconds, limit):
        result = [job for job in self.jobs.values() if job.next_run_at <= now][:limit]
        self.claimed.append((now, lease_seconds, limit))
        return tuple(result)

    async def finish_success(self, job, *, ran_at, next_run_at):
        self.successes.append((job.key, ran_at, next_run_at))
        if next_run_at is None:
            self.jobs.pop((job.guild_id, job.skill_id, job.key), None)
        else:
            self.jobs[(job.guild_id, job.skill_id, job.key)] = ScheduledJob(
                guild_id=job.guild_id,
                skill_id=job.skill_id,
                key=job.key,
                handler_id=job.handler_id,
                schedule=job.schedule,
                payload=job.payload,
                next_run_at=next_run_at,
                last_run_at=ran_at,
                failure_count=0,
            )

    async def finish_failure(self, job, *, error_code, next_run_at):
        self.failures.append((job.key, error_code, next_run_at))

    async def defer_job(self, job, *, next_run_at):
        self.deferred.append((job.key, next_run_at))


class ScheduleContractTests(unittest.TestCase):
    def test_interval_minimum_and_roundtrip(self):
        with self.assertRaisesRegex(ValueError, "at least 60"):
            IntervalSchedule(59)
        schedule = IntervalSchedule(3600)
        self.assertEqual(next_run_at(schedule, after=100), 3700)
        self.assertEqual(schedule_from_dict(schedule_to_dict(schedule)), schedule)

    def test_once_executes_only_if_future(self):
        schedule = OnceSchedule(200)
        self.assertEqual(next_run_at(schedule, after=100), 200)
        self.assertIsNone(next_run_at(schedule, after=200))

    def test_daily_uses_explicit_timezone(self):
        zone = ZoneInfo("Europe/Berlin")
        after = int(datetime(2026, 10, 3, 11, 0, tzinfo=zone).timestamp())
        expected = int(datetime(2026, 10, 3, 12, 0, tzinfo=zone).timestamp())
        self.assertEqual(
            next_run_at(DailySchedule(12, 0, "Europe/Berlin"), after=after),
            expected,
        )

    def test_weekly_uses_monday_zero(self):
        zone = ZoneInfo("Europe/Berlin")
        # Saturday 2026-10-03 -> next Monday 2026-10-05.
        after = int(datetime(2026, 10, 3, 12, 0, tzinfo=zone).timestamp())
        expected = int(datetime(2026, 10, 5, 10, 30, tzinfo=zone).timestamp())
        self.assertEqual(
            next_run_at(WeeklySchedule(0, 10, 30, "Europe/Berlin"), after=after),
            expected,
        )

    def test_nonexistent_dst_time_is_skipped(self):
        zone = ZoneInfo("Europe/Berlin")
        # 2026-03-29 02:30 does not exist. The next valid 02:30 is March 30.
        after = int(datetime(2026, 3, 28, 23, 0, tzinfo=zone).timestamp())
        expected = int(datetime(2026, 3, 30, 2, 30, tzinfo=zone).timestamp())
        self.assertEqual(
            next_run_at(DailySchedule(2, 30, "Europe/Berlin"), after=after),
            expected,
        )


class SchedulerEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = SkillRegistry()
        self.registry.register(FakeSkill())
        self.store = FakeStore()
        self.enabled = True

        async def availability(*, guild_id, skill_id):
            return self.enabled

        self.engine = SchedulerEngine(
            self.registry,
            self.store,
            availability=availability,
            lease_seconds=60,
        )
        self.calls = []

        async def handler(job):
            self.calls.append((job.guild_id, job.key, dict(job.payload)))

        self.engine.register_handler(
            skill_id="scheduler-skill",
            handler_id="post.execute.v1",
            handler=handler,
        )

    async def test_upsert_is_stable_by_guild_skill_key(self):
        await self.engine.upsert_job(
            guild_id=1,
            skill_id="scheduler-skill",
            key="post:1",
            handler_id="post.execute.v1",
            schedule=IntervalSchedule(60),
            payload={"version": 1},
            now=100,
        )
        await self.engine.upsert_job(
            guild_id=1,
            skill_id="scheduler-skill",
            key="post:1",
            handler_id="post.execute.v1",
            schedule=IntervalSchedule(120),
            payload={"version": 2},
            now=100,
        )
        self.assertEqual(len(self.store.jobs), 1)
        job = next(iter(self.store.jobs.values()))
        self.assertEqual(job.next_run_at, 220)
        self.assertEqual(dict(job.payload), {"version": 2})

    async def test_due_job_executes_and_moves_to_next_occurrence(self):
        await self.engine.upsert_job(
            guild_id=1,
            skill_id="scheduler-skill",
            key="post:1",
            handler_id="post.execute.v1",
            schedule=IntervalSchedule(60),
            payload={"x": 1},
            now=100,
        )
        report = await self.engine.run_due(now=160)
        self.assertEqual(report.executed, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(self.calls, [(1, "post:1", {"x": 1})])
        self.assertEqual(self.store.successes[-1], ("post:1", 160, 220))

    async def test_disabled_recurring_job_is_not_executed(self):
        await self.engine.upsert_job(
            guild_id=1,
            skill_id="scheduler-skill",
            key="post:1",
            handler_id="post.execute.v1",
            schedule=IntervalSchedule(60),
            payload={},
            now=100,
        )
        self.enabled = False
        report = await self.engine.run_due(now=160)
        self.assertEqual(report.deferred_disabled, 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.deferred[-1], ("post:1", 220))

    async def test_disabled_due_one_shot_is_retained(self):
        job = ScheduledJob(
            guild_id=1,
            skill_id="scheduler-skill",
            key="once:1",
            handler_id="post.execute.v1",
            schedule=OnceSchedule(150),
            payload={},
            next_run_at=150,
        )
        await self.store.upsert_job(job)
        self.enabled = False
        report = await self.engine.run_due(now=160)
        self.assertEqual(report.deferred_disabled, 1)
        self.assertEqual(self.store.deferred[-1], ("once:1", 220))

    async def test_failure_is_recorded_without_leaking_exception_message(self):
        async def broken(job):
            raise RuntimeError("private payload detail")
        self.engine.unregister_skill(skill_id="scheduler-skill")
        self.engine.register_handler(
            skill_id="scheduler-skill",
            handler_id="post.execute.v1",
            handler=broken,
        )
        await self.engine.upsert_job(
            guild_id=1,
            skill_id="scheduler-skill",
            key="post:1",
            handler_id="post.execute.v1",
            schedule=IntervalSchedule(60),
            payload={},
            now=100,
        )
        report = await self.engine.run_due(now=160)
        self.assertEqual(report.failed, 1)
        key, code, next_run = self.store.failures[-1]
        self.assertEqual(key, "post:1")
        self.assertEqual(code, "RuntimeError")
        self.assertNotIn("private payload detail", code)
        self.assertEqual(next_run, 220)

    async def test_missing_handler_is_failure_not_crash(self):
        job = ScheduledJob(
            guild_id=1,
            skill_id="scheduler-skill",
            key="missing",
            handler_id="missing.execute.v1",
            schedule=IntervalSchedule(60),
            payload={},
            next_run_at=100,
        )
        await self.store.upsert_job(job)
        report = await self.engine.run_due(now=100)
        self.assertEqual(report.unavailable_handlers, 1)
        self.assertEqual(self.store.failures[-1][1], "handler_unavailable")

    async def test_scheduler_capability_is_required(self):
        self.registry.register(FakeSkill("no-scheduler", scheduler=False))
        with self.assertRaisesRegex(PermissionError, "scheduler.jobs"):
            self.engine.register_handler(
                skill_id="no-scheduler",
                handler_id="job.execute.v1",
                handler=lambda job: None,
            )

    async def test_remove_job_is_idempotent(self):
        await self.engine.upsert_job(
            guild_id=1,
            skill_id="scheduler-skill",
            key="post:1",
            handler_id="post.execute.v1",
            schedule=IntervalSchedule(60),
            payload={},
            now=100,
        )
        self.assertTrue(await self.engine.remove_job(
            guild_id=1, skill_id="scheduler-skill", key="post:1"
        ))
        self.assertFalse(await self.engine.remove_job(
            guild_id=1, skill_id="scheduler-skill", key="post:1"
        ))


if __name__ == "__main__":
    unittest.main()
