import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import db
from hosts.gamerhq.skill_scheduler import GamerHQSchedulerStore
from skill_runtime.contracts.schedule import IntervalSchedule, OnceSchedule
from skill_runtime.runtime.scheduler import ScheduledJob


class GamerHQSchedulerStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "scheduler.db")
        self.db_patch.start()
        db.init_db()
        self.store = GamerHQSchedulerStore()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def job(self, *, key="post-1", next_run=100, schedule=None):
        return ScheduledJob(
            guild_id=1,
            skill_id="recurring-posts",
            key=key,
            handler_id="recurring-post.execute.v1",
            schedule=schedule or IntervalSchedule(60),
            payload={"postId": key},
            next_run_at=next_run,
        )

    async def test_upsert_is_idempotent_and_revision_increments_on_edit(self):
        await self.store.upsert_job(self.job(next_run=100))
        first = (await self.store.list_jobs(guild_id=1))[0]
        self.assertEqual(first.revision, 1)

        await self.store.upsert_job(self.job(next_run=200))
        jobs = await self.store.list_jobs(guild_id=1)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].next_run_at, 200)
        self.assertEqual(jobs[0].revision, 2)

    async def test_claim_is_atomic_until_lease_expires(self):
        await self.store.upsert_job(self.job())
        first = await self.store.claim_due(now=100, lease_seconds=120, limit=10)
        second = await self.store.claim_due(now=100, lease_seconds=120, limit=10)

        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].claim_token)
        self.assertEqual(second, ())

        after_expiry = await self.store.claim_due(now=221, lease_seconds=120, limit=10)
        self.assertEqual(len(after_expiry), 1)
        self.assertNotEqual(after_expiry[0].claim_token, first[0].claim_token)

    async def test_stale_claim_cannot_finish_after_new_worker_claims(self):
        await self.store.upsert_job(self.job())
        first = (await self.store.claim_due(now=100, lease_seconds=30, limit=1))[0]
        second = (await self.store.claim_due(now=131, lease_seconds=30, limit=1))[0]

        with self.assertRaisesRegex(RuntimeError, "stale"):
            await self.store.finish_success(first, ran_at=132, next_run_at=200)

        await self.store.finish_success(second, ran_at=132, next_run_at=200)
        current = (await self.store.list_jobs(guild_id=1))[0]
        self.assertEqual(current.next_run_at, 200)
        self.assertEqual(current.last_run_at, 132)

    async def test_edit_during_execution_invalidates_old_completion(self):
        await self.store.upsert_job(self.job(next_run=100))
        old = (await self.store.claim_due(now=100, lease_seconds=120, limit=1))[0]

        await self.store.upsert_job(self.job(next_run=500))

        with self.assertRaisesRegex(RuntimeError, "stale"):
            await self.store.finish_success(old, ran_at=101, next_run_at=160)

        current = (await self.store.list_jobs(guild_id=1))[0]
        self.assertEqual(current.next_run_at, 500)
        self.assertEqual(current.revision, 2)

    async def test_one_shot_success_deletes_only_owned_revision(self):
        await self.store.upsert_job(
            self.job(schedule=OnceSchedule(100), next_run=100)
        )
        claimed = (await self.store.claim_due(now=100, lease_seconds=60, limit=1))[0]
        await self.store.finish_success(claimed, ran_at=100, next_run_at=None)
        self.assertEqual(await self.store.list_jobs(guild_id=1), ())

    async def test_failure_releases_lease_and_records_bounded_error_code(self):
        await self.store.upsert_job(self.job())
        claimed = (await self.store.claim_due(now=100, lease_seconds=60, limit=1))[0]

        await self.store.finish_failure(
            claimed,
            error_code="x" * 500,
            next_run_at=160,
        )

        with db.connect() as conn:
            row = conn.execute("SELECT * FROM skill_jobs").fetchone()
        self.assertEqual(row["failure_count"], 1)
        self.assertEqual(len(row["last_error_code"]), 80)
        self.assertEqual(row["lease_until"], 0)
        self.assertIsNone(row["lease_token"])
        self.assertEqual(row["next_run_at"], 160)

    async def test_remove_job_is_scoped_to_guild_skill_key(self):
        await self.store.upsert_job(self.job(key="same"))
        await self.store.upsert_job(ScheduledJob(
            guild_id=2,
            skill_id="recurring-posts",
            key="same",
            handler_id="recurring-post.execute.v1",
            schedule=IntervalSchedule(60),
            payload={},
            next_run_at=100,
        ))

        self.assertTrue(await self.store.remove_job(
            guild_id=1,
            skill_id="recurring-posts",
            key="same",
        ))
        self.assertEqual(len(await self.store.list_jobs(guild_id=2)), 1)

    async def test_malformed_persisted_job_is_quarantined_not_claimed_forever(self):
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_jobs(
                    guild_id,skill_id,job_key,handler_id,schedule_json,payload_json,
                    enabled,next_run_at,last_run_at,failure_count,revision,last_error_code,
                    lease_until,lease_token,updated_at
                ) VALUES(1,'recurring-posts','bad','recurring-post.execute.v1',
                         '{bad json}','{}',1,100,NULL,0,1,NULL,0,NULL,100)
                """
            )

        self.assertEqual(
            await self.store.claim_due(now=100, lease_seconds=60, limit=10),
            (),
        )
        with db.connect() as conn:
            row = conn.execute("SELECT enabled,last_error_code FROM skill_jobs").fetchone()
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["last_error_code"], "invalid_persisted_job")


if __name__ == "__main__":
    unittest.main()
