import unittest

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.events import EventContract, EventEnvelope
from skill_runtime.contracts.manifest import SkillEvents, SkillManifest
from skill_runtime.runtime.event_bus import EventBus
from skill_runtime.runtime.registry import SkillRegistry


class FakeSkill:
    def __init__(self, skill_id, *, emits=(), consumes=(), permissions=()):
        self.manifest = SkillManifest(
            id=skill_id,
            name=skill_id.title(),
            version="1.0.0",
            runtime_api_version="1",
            description="fixture",
            author="test",
            permissions=tuple(permissions),
            events=SkillEvents(
                emits=tuple(EventContract(value) for value in emits),
                consumes=tuple(EventContract(value) for value in consumes),
            ),
        )


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = SkillRegistry()
        self.registry.register(FakeSkill(
            "producer",
            emits=("demo.sent.v1",),
            permissions=(SkillCapability.EVENTS_EMIT.value,),
        ))
        self.registry.register(FakeSkill(
            "consumer",
            consumes=("demo.sent.v1",),
            permissions=(SkillCapability.EVENTS_SUBSCRIBE.value,),
        ))
        self.bus = EventBus(self.registry)

    def event(self, guild_id=1):
        return EventEnvelope(
            event_id="demo.sent.v1",
            producer_skill_id="producer",
            guild_id=guild_id,
            occurred_at=123,
            payload={"id": "x"},
        )

    async def test_declared_event_delivered_only_within_same_guild(self):
        seen = []
        async def handler(event):
            seen.append((event.guild_id, event.payload["id"]))

        await self.bus.subscribe(
            guild_id=1,
            consumer_skill_id="consumer",
            event_id="demo.sent.v1",
            handler=handler,
        )
        report = await self.bus.emit(self.event(guild_id=2))
        self.assertEqual(report.delivered, 0)
        self.assertEqual(seen, [])

        report = await self.bus.emit(self.event(guild_id=1))
        self.assertEqual(report.delivered, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(seen, [(1, "x")])

    async def test_subscribe_requires_declared_contract_and_capability(self):
        self.registry.register(FakeSkill("undeclared", permissions=(SkillCapability.EVENTS_SUBSCRIBE.value,)))
        with self.assertRaisesRegex(PermissionError, "did not declare consumed event"):
            await self.bus.subscribe(
                guild_id=1,
                consumer_skill_id="undeclared",
                event_id="demo.sent.v1",
                handler=lambda event: None,
            )

        self.registry.register(FakeSkill("no-cap", consumes=("demo.sent.v1",)))
        async def handler(event): pass
        with self.assertRaisesRegex(PermissionError, "events.subscribe"):
            await self.bus.subscribe(
                guild_id=1,
                consumer_skill_id="no-cap",
                event_id="demo.sent.v1",
                handler=handler,
            )

    async def test_emit_requires_declared_contract_and_capability(self):
        self.registry.register(FakeSkill(
            "no-cap-producer",
            emits=("demo.sent.v1",),
        ))
        event = EventEnvelope(
            event_id="demo.sent.v1",
            producer_skill_id="no-cap-producer",
            guild_id=1,
            occurred_at=1,
        )
        with self.assertRaisesRegex(PermissionError, "events.emit"):
            await self.bus.emit(event)

        self.registry.register(FakeSkill(
            "other-producer",
            emits=("demo.other.v1",),
            permissions=(SkillCapability.EVENTS_EMIT.value,),
        ))
        event = EventEnvelope(
            event_id="demo.sent.v1",
            producer_skill_id="other-producer",
            guild_id=1,
            occurred_at=1,
        )
        with self.assertRaisesRegex(PermissionError, "did not declare emitted event"):
            await self.bus.emit(event)

    async def test_failing_consumer_does_not_prevent_other_consumers(self):
        self.registry.register(FakeSkill(
            "consumer-two",
            consumes=("demo.sent.v1",),
            permissions=(SkillCapability.EVENTS_SUBSCRIBE.value,),
        ))
        seen = []
        async def broken(event):
            raise RuntimeError("private internal failure")
        async def healthy(event):
            seen.append(event.event_id)

        await self.bus.subscribe(guild_id=1, consumer_skill_id="consumer", event_id="demo.sent.v1", handler=broken)
        await self.bus.subscribe(guild_id=1, consumer_skill_id="consumer-two", event_id="demo.sent.v1", handler=healthy)

        report = await self.bus.emit(self.event())
        self.assertEqual(report.delivered, 1)
        self.assertEqual(report.failed_consumers, ("consumer",))
        self.assertEqual(seen, ["demo.sent.v1"])

    async def test_disabled_consumer_is_not_called_when_host_provides_availability(self):
        async def availability(guild_id, skill_id):
            return skill_id != "consumer"
        bus = EventBus(self.registry, availability=availability)
        seen = []
        async def handler(event):
            seen.append(event.event_id)
        await bus.subscribe(
            guild_id=1,
            consumer_skill_id="consumer",
            event_id="demo.sent.v1",
            handler=handler,
        )
        report = await bus.emit(self.event())
        self.assertEqual(report.delivered, 0)
        self.assertEqual(seen, [])

    async def test_subscription_is_idempotent_and_removable_by_skill_lifecycle(self):
        async def handler(event): pass
        self.assertTrue(await self.bus.subscribe(
            guild_id=1, consumer_skill_id="consumer", event_id="demo.sent.v1", handler=handler
        ))
        self.assertFalse(await self.bus.subscribe(
            guild_id=1, consumer_skill_id="consumer", event_id="demo.sent.v1", handler=handler
        ))
        self.assertEqual(await self.bus.unsubscribe_skill(guild_id=1, skill_id="consumer"), 1)
        self.assertEqual((await self.bus.emit(self.event())).delivered, 0)


if __name__ == "__main__":
    unittest.main()
