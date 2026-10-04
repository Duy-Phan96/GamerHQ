import unittest
from types import SimpleNamespace

from skills.recurring_posts import (
    HANDLER_ID,
    MIN_INTERVAL_SECONDS,
    RecurringPostsSkill,
)


class FakeStorage:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)


class FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.removed = []

    async def upsert_job(self, *, key, handler_id, schedule, payload):
        self.jobs[key] = {
            "handler_id": handler_id,
            "schedule": dict(schedule),
            "payload": dict(payload),
        }

    async def remove_job(self, *, key):
        self.removed.append(key)
        self.jobs.pop(key, None)


class FakeDiscord:
    def __init__(self):
        self.channels = {10: SimpleNamespace(id=10, name="announcements", kind="text")}
        self.sent = []
        self.fail = False

    async def get_channel(self, *, channel_id):
        if channel_id not in self.channels:
            raise KeyError("missing")
        return self.channels[channel_id]

    async def send_message(self, *, channel_id, content=None, embed=None, allowed_mentions=None):
        if self.fail:
            raise RuntimeError("synthetic")
        self.sent.append((channel_id, content))
        return 9000 + len(self.sent)


class FakeAudit:
    def __init__(self):
        self.calls = []

    async def write(self, **kwargs):
        self.calls.append(kwargs)


class FakeEvents:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)
        return SimpleNamespace(delivered=0, failed_consumers=())


class FakeRegistrationScheduler:
    def __init__(self):
        self.handlers = {}

    def register_handler(self, handler_id, handler):
        self.handlers[handler_id] = handler


class RecurringPostsSkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.skill = RecurringPostsSkill()
        self.storage = FakeStorage()
        self.scheduler = FakeScheduler()
        self.discord = FakeDiscord()
        self.audit = FakeAudit()
        self.events = FakeEvents()
        self.ctx = SimpleNamespace(
            guild_id=1,
            skill_id="recurring-posts",
            storage=self.storage,
            scheduler=self.scheduler,
            discord=self.discord,
            audit=self.audit,
            events=self.events,
        )

    async def create(self, **overrides):
        values = dict(
            name="Rules reminder",
            channel_id=10,
            content="Please remember the rules.",
            schedule={"type": "interval", "seconds": MIN_INTERVAL_SECONDS},
        )
        values.update(overrides)
        return await self.skill.create_post(self.ctx, **values)

    async def test_registration_binds_one_stable_versioned_handler(self):
        registration = FakeRegistrationScheduler()
        await self.skill.register(SimpleNamespace(scheduler=registration))
        self.assertEqual(tuple(registration.handlers), (HANDLER_ID,))

    async def test_create_persists_configuration_and_upserts_scheduler_job(self):
        post = await self.create()
        stored = await self.skill.get_post(self.ctx, post.id)
        self.assertEqual(stored.content, "Please remember the rules.")
        job = self.scheduler.jobs[f"post:{post.id}"]
        self.assertEqual(job["handler_id"], HANDLER_ID)
        self.assertEqual(job["payload"], {"postId": post.id})
        self.assertEqual(job["schedule"]["seconds"], MIN_INTERVAL_SECONDS)

    async def test_interval_below_skill_antispam_minimum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least 15 minutes"):
            await self.create(schedule={"type": "interval", "seconds": 60})
        self.assertEqual(self.scheduler.jobs, {})
        self.assertEqual(await self.skill.list_posts(self.ctx), ())

    async def test_daily_and_weekly_schedules_are_supported(self):
        daily = await self.create(
            name="Daily",
            schedule={"type": "daily", "hour": 9, "minute": 30, "timezone": "Europe/Berlin"},
        )
        weekly = await self.create(
            name="Weekly",
            schedule={
                "type": "weekly",
                "weekday": 0,
                "hour": 10,
                "minute": 0,
                "timezone": "Europe/Berlin",
            },
        )
        self.assertEqual(self.scheduler.jobs[f"post:{daily.id}"]["schedule"]["type"], "daily")
        self.assertEqual(self.scheduler.jobs[f"post:{weekly.id}"]["schedule"]["type"], "weekly")

    async def test_pause_removes_job_but_keeps_configuration(self):
        post = await self.create()
        paused = await self.skill.set_active(self.ctx, post_id=post.id, active=False)
        self.assertFalse(paused.active)
        self.assertNotIn(f"post:{post.id}", self.scheduler.jobs)
        self.assertEqual((await self.skill.get_post(self.ctx, post.id)).content, post.content)

    async def test_resume_recreates_job_without_duplicate_configuration(self):
        post = await self.create()
        await self.skill.set_active(self.ctx, post_id=post.id, active=False)
        await self.skill.set_active(self.ctx, post_id=post.id, active=True)
        self.assertIn(f"post:{post.id}", self.scheduler.jobs)
        self.assertEqual(len(await self.skill.list_posts(self.ctx)), 1)

    async def test_delete_removes_job_and_configuration(self):
        post = await self.create()
        await self.skill.delete_post(self.ctx, post_id=post.id)
        self.assertEqual(await self.skill.list_posts(self.ctx), ())
        self.assertNotIn(f"post:{post.id}", self.scheduler.jobs)

    async def test_execution_sends_once_for_same_scheduler_slot(self):
        post = await self.create()
        job = SimpleNamespace(payload={"postId": post.id}, next_run_at=12345)

        await self.skill._execute(self.ctx, job)
        await self.skill._execute(self.ctx, job)

        self.assertEqual(self.discord.sent, [(10, "Please remember the rules.")])
        stored = await self.skill.get_post(self.ctx, post.id)
        self.assertEqual(stored.last_sent_slot, 12345)
        self.assertEqual(stored.last_message_id, 9001)
        self.assertEqual(len(self.events.events), 1)

    async def test_known_send_failure_clears_pending_reservation_for_retry(self):
        post = await self.create()
        job = SimpleNamespace(payload={"postId": post.id}, next_run_at=12345)
        self.discord.fail = True

        with self.assertRaises(RuntimeError):
            await self.skill._execute(self.ctx, job)

        stored = await self.skill.get_post(self.ctx, post.id)
        self.assertIsNone(stored.pending_slot)
        self.assertIsNone(stored.last_sent_slot)

        self.discord.fail = False
        await self.skill._execute(self.ctx, job)
        self.assertEqual(len(self.discord.sent), 1)

    async def test_missing_or_paused_post_does_not_send(self):
        await self.skill._execute(
            self.ctx,
            SimpleNamespace(payload={"postId": "missing"}, next_run_at=12345),
        )
        self.assertEqual(self.discord.sent, [])

        post = await self.create()
        await self.skill.set_active(self.ctx, post_id=post.id, active=False)
        await self.skill._execute(
            self.ctx,
            SimpleNamespace(payload={"postId": post.id}, next_run_at=12346),
        )
        self.assertEqual(self.discord.sent, [])


if __name__ == "__main__":
    unittest.main()
