import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from database import db
from hosts.gamerhq.skill_scheduler import GamerHQSchedulerStore
from skill_runtime.contracts.schedule import IntervalSchedule, OnceSchedule
from skill_runtime.runtime.scheduler import ScheduledJob


class GamerHQSchedulerStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "scheduler.db"
        self.db_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()
        self.store = GamerHQSchedulerStore()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def job(self, *, key="post:1", next_run=100, schedule=None, payload=None):
        return ScheduledJob(
            guild_id=1,
            skill_id="recurring-posts",
            key=key,
            handler_id="post.execute.v1",
            schedule=schedule or IntervalSchedule(60),
            payload=MappingProxyType(payload or {"id": key}),
            next_run_at=next_run,
        )

    def rows(self):
        with db.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM skill_jobs ORDER BY guild_id,skill_id,job_key"
            )]

    async def test_upsert_is_idempotent_and_increments_revision(self):
        await self.store.upsert_job(self.job(payload={"version": 1}))
        await self.store.upsert_job(self.job(next_run=200, payload={"version": 2}))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["next_run_at"], 200)
        self.assertEqual(rows[0]["revision"], 2)
        self.assertIn('"version":2', rows[0]["payload_json"])

    async def test_claim_due_is_atomic_and_lease_prevents_duplicate_claim(self):
        await self.store.upsert_job(self.job(next_run=100))
        first = await self.store.claim_due(now=100, lease_seconds=60, limit=10)
        second = await GamerHQSchedulerStore().claim_due(now=100, lease_seconds=60, limit=10)
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].claim_token)
        self.assertEqual(second, ())
        third = await GamerHQSchedulerStore().claim_due(now=161, lease_seconds=60, limit=10)
        self.assertEqual(len(third), 1)
        self.assertNotEqual(first[0].claim_token, third[0].claim_token)

    async def test_successful_recurring_claim_advances_and_clears_lease(self):
        await self.store.upsert_job(self.job(next_run=100))
        claimed = (await self.store.claim_due(now=100, lease_seconds=60, limit=1))[0]
        await self.store.finish_success(claimed, ran_at=100, next_run_at=160)
        row = self.rows()[0]
        self.assertEqual(row["next_run_at"], 160)
        self.assertEqual(row["last_run_at"], 100)
        self.assertEqual(row["failure_count"], 0)
        self.assertIsNone(row["lease_token"])
        self.assertEqual(row["lease_until"], 0)

    async def test_successful_one_shot_claim_is_removed(self):
        await self.store.upsert_job(self.job(
            key="once",
            next_run=100,
            schedule=OnceSchedule(100),
        ))
        claimed = (await self.store.claim_due(now=100, lease_seconds=60, limit=1))[0]
        await self.store.finish_success(claimed, ran_at=100, next_run_at=None)
        self.assertEqual(self.rows(), [])

    async def test_failure_records_only_bounded_error_code(self):
        await self.store.upsert_job(self.job())
        claimed = (await self.store.claim_due(now=100, lease_seconds=60, limit=1))[0]
        await self.store.finish_failure(
            claimed,
            error_code="X" * 200,
            next_run_at=160,
        )
        row = self.rows()[0]
        self.assertEqual(row["failure_count"], 1)
        self.assertEqual(len(row["last_error_code"]), 80)
        self.assertIsNone(row["lease_token"])

    async def test_defer_does_not_count_as_failure_or_run(self):
        await self.store.upsert_job(self.job())
        claimed = (await self.store.claim_due(now=100, lease_seconds=60, limit=1))[0]
        await self.store.defer_job(claimed, next_run_at=160)
        row = self.rows()[0]
        self.assertEqual(row["next_run_at"], 160)
        self.assertEqual(row["failure_count"], 0)
        self.assertIsNone(row["last_run_at"])
        self.assertIsNone(row["lease_token"])

    async def test_edit_while_claimed_invalidates_stale_worker_completion(self):
        await self.store.upsert_job(self.job(payload={"version": 1}))
        claimed = (await self.store.claim_due(now=100, lease_seconds=60, limit=1))[0]
        await self.store.upsert_job(self.job(next_run=300, payload={"version": 2}))
        with self.assertRaisesRegex(RuntimeError, "stale"):
            await self.store.finish_success(claimed, ran_at=100, next_run_at=160)
        row = self.rows()[0]
        self.assertEqual(row["revision"], 2)
        self.assertEqual(row["next_run_at"], 300)
        self.assertIn('"version":2', row["payload_json"])

    async def test_malformed_persisted_job_is_disabled_and_not_claimed(self):
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_jobs(
                    guild_id,skill_id,job_key,handler_id,schedule_json,payload_json,
                    enabled,next_run_at,last_run_at,failure_count,revision,last_error_code,
                    lease_until,lease_token,updated_at
                ) VALUES(1,'recurring-posts','bad','post.execute.v1',
                         '{"type":"interval","seconds":"not-a-number"}','{}',
                         1,100,NULL,0,1,NULL,0,NULL,100)
                """
            )
        claimed = await self.store.claim_due(now=100, lease_seconds=60, limit=10)
        self.assertEqual(claimed, ())
        row = self.rows()[0]
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["last_error_code"], "invalid_persisted_job")
        self.assertIsNone(row["lease_token"])

    async def test_remove_is_scoped_and_idempotent(self):
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
        self.assertFalse(await self.store.remove_job(
            guild_id=1, skill_id="recurring-posts", key="post:1"
        ))
        self.assertEqual([row["guild_id"] for row in self.rows()], [2])


if __name__ == "__main__":
    unittest.main()
