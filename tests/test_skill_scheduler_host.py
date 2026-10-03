import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from database import db
from hosts.gamerhq.scheduler_host import GamerHQSkillJobStore
from skill_runtime.contracts.schedule import IntervalSchedule, OnceSchedule
from skill_runtime.runtime.scheduler import ScheduledJob


class GamerHQSkillSchedulerStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "scheduler.db"
        self.db_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()
        self.store = GamerHQSkillJobStore()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def job(self, *, key="post:1", next_run=100, schedule=None):
        return ScheduledJob(
            guild_id=1,
            skill_id="recurring-posts",
            key=key,
            handler_id="post.execute.v1",
            schedule=schedule or IntervalSchedule(60),
            payload=MappingProxyType({"id": key}),
            next_run_at=next_run,
        )

    async def test_upsert_is_idempotent_and_persists_payload(self):
        await self.store.upsert_job(self.job())
        changed = self.job(next_run=200)
        await self.store.upsert_job(changed)
        with db.connect() as conn:
            rows = conn.execute("SELECT * FROM skill_jobs").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["next_run_at"], 200)
        self.assertIn('"id":"post:1"', rows[0]["payload_json"])

    async def test_claim_due_respects_lease(self):
        await self.store.upsert_job(self.job(next_run=100))
        first = await self.store.claim_due(now=100, lease_seconds=60, limit=10)
        second = await self.store.claim_due(now=100, lease_seconds=60, limit=10)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())
        third = await self.store.claim_due(now=161, lease_seconds=60, limit=10)
        self.assertEqual(len(third), 1)

    async def test_success_advances_or_finishes_job(self):
        recurring = self.job(next_run=100)
        await self.store.upsert_job(recurring)
        await self.store.finish_success(recurring, ran_at=100, next_run_at=160)
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM skill_jobs WHERE job_key='post:1'").fetchone()
        self.assertEqual(row["enabled"], 1)
        self.assertEqual(row["next_run_at"], 160)
        self.assertEqual(row["last_run_at"], 100)
        self.assertEqual(row["failure_count"], 0)

        once = self.job(key="once", next_run=100, schedule=OnceSchedule(100))
        await self.store.upsert_job(once)
        await self.store.finish_success(once, ran_at=100, next_run_at=None)
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM skill_jobs WHERE job_key='once'").fetchone()
        self.assertEqual(row["enabled"], 0)
        self.assertIsNone(row["next_run_at"])

    async def test_failure_records_only_bounded_error_code(self):
        job = self.job()
        await self.store.upsert_job(job)
        await self.store.finish_failure(job, error_code="X" * 200, next_run_at=160)
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM skill_jobs WHERE job_key='post:1'").fetchone()
        self.assertEqual(row["failure_count"], 1)
        self.assertEqual(len(row["last_error_code"]), 80)
        self.assertEqual(row["lease_until"], 0)

    async def test_defer_does_not_count_as_failure_or_run(self):
        job = self.job()
        await self.store.upsert_job(job)
        await self.store.defer_job(job, next_run_at=160)
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM skill_jobs WHERE job_key='post:1'").fetchone()
        self.assertEqual(row["next_run_at"], 160)
        self.assertEqual(row["failure_count"], 0)
        self.assertIsNone(row["last_run_at"])

    async def test_malformed_persisted_job_is_disabled_and_not_returned(self):
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_jobs(
                    guild_id,skill_id,job_key,handler_id,schedule_json,payload_json,
                    enabled,next_run_at,last_run_at,failure_count,last_error_code,
                    lease_until,updated_at
                ) VALUES(1,'recurring-posts','bad','post.execute.v1','{"type":"interval","seconds":"not-a-number"}',
                         '{}',1,100,NULL,0,NULL,0,100)
                """
            )
        claimed = await self.store.claim_due(now=100, lease_seconds=60, limit=10)
        self.assertEqual(claimed, ())
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM skill_jobs WHERE job_key='bad'").fetchone()
        self.assertEqual(row["enabled"], 0)
        self.assertIsNone(row["next_run_at"])
        self.assertEqual(row["last_error_code"], "invalid_configuration")

    async def test_remove_is_scoped_by_guild_skill_key(self):
        await self.store.upsert_job(self.job())
        other = ScheduledJob(
            guild_id=2,
            skill_id="recurring-posts",
            key="post:1",
            handler_id="post.execute.v1",
            schedule=IntervalSchedule(60),
            payload={},
            next_run_at=100,
        )
        await self.store.upsert_job(other)
        self.assertTrue(await self.store.remove_job(
            guild_id=1, skill_id="recurring-posts", key="post:1"
        ))
        with db.connect() as conn:
            rows = conn.execute("SELECT guild_id FROM skill_jobs").fetchall()
        self.assertEqual([row["guild_id"] for row in rows], [2])


if __name__ == "__main__":
    unittest.main()
