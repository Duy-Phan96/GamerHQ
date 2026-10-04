import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import db
from hosts.gamerhq.scheduler_store import GamerHQSchedulerStore
from skill_runtime.contracts.schedule import IntervalSchedule, OnceSchedule
from skill_runtime.runtime.scheduler import ScheduledJob


class GamerHQSchedulerStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "scheduler.db"
        self.patch = patch.object(db, "DB_PATH", self.path)
        self.patch.start()
        db.init_db()
        self.store = GamerHQSchedulerStore()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def job(self, *, key="post-1", next_run=100, schedule=None, payload=None):
        return ScheduledJob(
            guild_id=1,
            skill_id="recurring-posts",
            key=key,
            handler_id="recurring-post.run.v1",
            schedule=schedule or IntervalSchedule(60),
            payload=payload or {"postId": "1"},
            next_run_at=next_run,
        )

    async def test_job_survives_new_store_instance(self):
        await self.store.upsert_job(self.job())
        restarted = GamerHQSchedulerStore()
        claimed = await restarted.claim_due(now=100, lease_seconds=60, limit=10)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].key, "post-1")
        self.assertEqual(dict(claimed[0].payload), {"postId": "1"})

    async def test_second_claim_cannot_take_active_lease(self):
        await self.store.upsert_job(self.job())
        first = await self.store.claim_due(now=100, lease_seconds=60, limit=10)
        second = await GamerHQSchedulerStore().claim_due(now=100, lease_seconds=60, limit=10)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())

    async def test_expired_lease_can_be_reclaimed_after_restart(self):
        await self.store.upsert_job(self.job())
        first = await self.store.claim_due(now=100, lease_seconds=60, limit=10)
        self.assertEqual(len(first), 1)
        reclaimed = await GamerHQSchedulerStore().claim_due(now=161, lease_seconds=60, limit=10)
        self.assertEqual(len(reclaimed), 1)
        self.assertNotEqual(first[0].claim_token, reclaimed[0].claim_token)

    async def test_upsert_invalidates_old_claim_and_increments_revision(self):
        await self.store.upsert_job(self.job(payload={"revision": 1}))
        old = (await self.store.claim_due(now=100, lease_seconds=60, limit=10))[0]

        await self.store.upsert_job(self.job(next_run=200, payload={"revision": 2}))

        with self.assertRaisesRegex(RuntimeError, "stale"):
            await self.store.finish_success(old, ran_at=100, next_run_at=160)

        self.assertEqual(await self.store.claim_due(now=150, lease_seconds=60, limit=10), ())
        new = (await self.store.claim_due(now=200, lease_seconds=60, limit=10))[0]
        self.assertEqual(dict(new.payload), {"revision": 2})
        self.assertGreater(new.revision, old.revision)

    async def test_failure_then_success_resets_failure_count(self):
        await self.store.upsert_job(self.job())
        first = (await self.store.claim_due(now=100, lease_seconds=60, limit=10))[0]
        await self.store.finish_failure(first, error_code="RuntimeError", next_run_at=160)

        failed = (await self.store.claim_due(now=160, lease_seconds=60, limit=10))[0]
        self.assertEqual(failed.failure_count, 1)

        await self.store.finish_success(failed, ran_at=160, next_run_at=220)
        next_job = (await self.store.claim_due(now=220, lease_seconds=60, limit=10))[0]
        self.assertEqual(next_job.failure_count, 0)
        self.assertEqual(next_job.last_run_at, 160)

    async def test_one_shot_success_removes_persisted_job(self):
        await self.store.upsert_job(self.job(key="once", schedule=OnceSchedule(100)))
        claimed = (await self.store.claim_due(now=100, lease_seconds=60, limit=10))[0]
        await self.store.finish_success(claimed, ran_at=100, next_run_at=None)
        self.assertEqual(
            await self.store.claim_due(now=1000, lease_seconds=60, limit=10),
            (),
        )

    async def test_remove_job_is_namespace_scoped(self):
        await self.store.upsert_job(self.job())
        await self.store.upsert_job(ScheduledJob(
            guild_id=2,
            skill_id="recurring-posts",
            key="post-1",
            handler_id="recurring-post.run.v1",
            schedule=IntervalSchedule(60),
            payload={},
            next_run_at=100,
        ))

        self.assertTrue(await self.store.remove_job(
            guild_id=1, skill_id="recurring-posts", key="post-1"
        ))
        claimed = await self.store.claim_due(now=100, lease_seconds=60, limit=10)
        self.assertEqual([(job.guild_id, job.key) for job in claimed], [(2, "post-1")])

    async def test_claim_order_is_deterministic_and_limited(self):
        for key, run_at in [("later", 90), ("first", 80), ("second", 80)]:
            await self.store.upsert_job(self.job(key=key, next_run=run_at))
        claimed = await self.store.claim_due(now=100, lease_seconds=60, limit=2)
        self.assertEqual([job.key for job in claimed], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
