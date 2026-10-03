import unittest
from types import MappingProxyType

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.events import EventContract, EventEnvelope
from skill_runtime.contracts.manifest import SkillEvents, SkillManifest, SkillPublicApis, validate_manifest
from skill_runtime.contracts.public_api import PublicApiContract


class SkillContractTests(unittest.TestCase):
    def manifest(self, **overrides):
        values = dict(
            id="recurring-posts",
            name="Recurring Posts",
            version="1.0.0",
            runtime_api_version="1",
            description="Automatically posts configured messages.",
            author="GamerHQ",
            permissions=(
                SkillCapability.DISCORD_MESSAGES_SEND.value,
                SkillCapability.SCHEDULER_JOBS.value,
                SkillCapability.STORAGE_SKILL.value,
                SkillCapability.EVENTS_EMIT.value,
            ),
            events=SkillEvents(
                emits=(EventContract("recurring-post.sent.v1", "Emitted after a confirmed send."),),
                consumes=(),
            ),
            public_apis=SkillPublicApis(),
        )
        values.update(overrides)
        return SkillManifest(**values)

    def test_valid_reference_manifest(self):
        manifest = self.manifest()
        self.assertEqual(manifest.id, "recurring-posts")
        self.assertEqual(manifest.runtime_api_version, "1")

    def test_invalid_skill_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "machine ID"):
            self.manifest(id="Recurring Posts")

    def test_invalid_semver_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "semantic versioning"):
            self.manifest(version="v1")

    def test_unsupported_runtime_api_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported Skill Runtime API"):
            self.manifest(runtime_api_version="999")

    def test_unknown_capability_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown Skill capability"):
            self.manifest(permissions=("database.raw",))

    def test_duplicate_capability_is_rejected(self):
        cap = SkillCapability.STORAGE_SKILL.value
        with self.assertRaisesRegex(ValueError, "Duplicate Skill capabilities"):
            self.manifest(permissions=(cap, cap))

    def test_event_ids_are_explicitly_versioned(self):
        with self.assertRaisesRegex(ValueError, "versioned"):
            EventContract("recurring-post.sent")
        self.assertEqual(EventContract("recurring-post.sent.v1").id, "recurring-post.sent.v1")

    def test_public_api_ids_are_explicitly_versioned(self):
        self.assertEqual(PublicApiContract("events.get-event.v1").id, "events.get-event.v1")
        with self.assertRaisesRegex(ValueError, "versioned"):
            PublicApiContract("events.get-event")

    def test_same_contract_cannot_be_both_emitted_and_consumed_in_manifest(self):
        event = EventContract("message.posted.v1")
        with self.assertRaisesRegex(ValueError, "Duplicate event contract"):
            self.manifest(events=SkillEvents(emits=(event,), consumes=(event,)))

    def test_public_api_contracts_are_declared_in_manifest(self):
        manifest = self.manifest(public_apis=SkillPublicApis(
            exposes=(PublicApiContract("recurring-post.get-config.v1"),),
            consumes=(PublicApiContract("events.get-event.v1"),),
        ))
        self.assertEqual(manifest.public_apis.exposes[0].id, "recurring-post.get-config.v1")

    def test_event_payload_is_immutable_at_contract_boundary(self):
        source = {"postId": "post-1"}
        event = EventEnvelope(
            event_id="recurring-post.sent.v1",
            producer_skill_id="recurring-posts",
            guild_id=123,
            occurred_at=100,
            payload=source,
        )
        source["postId"] = "mutated"
        self.assertEqual(event.payload["postId"], "post-1")
        self.assertIsInstance(event.payload, MappingProxyType)

    def test_validation_can_be_reused_by_future_host_with_supported_versions(self):
        manifest = object.__new__(SkillManifest)
        object.__setattr__(manifest, "id", "future-skill")
        object.__setattr__(manifest, "name", "Future")
        object.__setattr__(manifest, "version", "1.0.0")
        object.__setattr__(manifest, "runtime_api_version", "2")
        object.__setattr__(manifest, "description", "")
        object.__setattr__(manifest, "author", "Developer")
        object.__setattr__(manifest, "permissions", ())
        object.__setattr__(manifest, "events", SkillEvents())
        object.__setattr__(manifest, "public_apis", SkillPublicApis())
        validate_manifest(manifest, supported_api_versions=frozenset({"1", "2"}))


if __name__ == "__main__":
    unittest.main()
