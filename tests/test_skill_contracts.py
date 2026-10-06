import unittest
from types import MappingProxyType

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.events import EventContract, EventEnvelope
from skill_runtime.contracts.manifest import SkillEvents, SkillManagementApis, SkillManifest, SkillPublicApis, validate_manifest
from skill_runtime.contracts.management import ManagementApiContract
from skill_runtime.contracts.management_ui import ManagementCollectionOperations, ManagementCollectionSchema, ManagementField, ManagementSection, ManagementUiSchema
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
            management_apis=SkillManagementApis(),
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

    def test_management_api_ids_are_explicitly_versioned(self):
        self.assertEqual(
            ManagementApiContract("recurring-posts.list.v1").id,
            "recurring-posts.list.v1",
        )
        with self.assertRaisesRegex(ValueError, "versioned"):
            ManagementApiContract("recurring-posts.list")

    def test_management_ui_schema_uses_safe_declarative_fields(self):
        schema = ManagementUiSchema(
            version="1",
            read_contract="recurring-posts.list.v1",
            write_contract="recurring-posts.update.v1",
            sections=(
                ManagementSection(
                    id="general",
                    title="General",
                    fields=(
                        ManagementField(
                            key="enabled",
                            label="Enabled",
                            type="boolean",
                            config_path="settings.enabled",
                        ),
                        ManagementField(
                            key="dailyCap",
                            label="Daily cap",
                            type="integer",
                            config_path="settings.dailyCap",
                            minimum=0,
                            maximum=1000,
                        ),
                    ),
                ),
            ),
        )
        self.assertEqual(schema.sections[0].fields[1].type, "integer")

    def test_collection_schema_declares_generic_crud_operations(self):
        collection = ManagementCollectionSchema(
            operations=ManagementCollectionOperations(
                list_contract="posts.list.v1",
                create_contract="posts.create.v1",
                get_contract="posts.get.v1",
                validate_contract="posts.validate.v1",
                update_contract="posts.update.v1",
                delete_preview_contract="posts.delete-preview.v1",
                delete_contract="posts.delete.v1",
            ),
            item_fields=(
                ManagementField(
                    key="name",
                    label="Name",
                    type="string",
                    config_path="name",
                    required=True,
                ),
                ManagementField(
                    key="channel",
                    label="Channel",
                    type="discord_channel",
                    config_path="channelId",
                    required=True,
                ),
            ),
            item_id_path="id",
            item_id_payload_key="postId",
            title_path="name",
            status_path="status",
            max_items=20,
        )
        field = ManagementField(
            key="posts",
            label="Posts",
            type="collection",
            config_path="posts",
            collection=collection,
        )
        self.assertEqual(field.collection.operations.update_contract, "posts.update.v1")
        self.assertEqual(field.collection.item_id_payload_key, "postId")
        self.assertEqual(field.collection.max_items, 20)

    def test_collection_item_id_payload_key_must_be_stable(self):
        with self.assertRaisesRegex(ValueError, "item_id_payload_key"):
            ManagementCollectionSchema(
                operations=ManagementCollectionOperations(
                    list_contract="posts.list.v1",
                    create_contract="posts.create.v1",
                ),
                item_fields=(),
                item_id_payload_key="Bad key",
            )

    def test_collection_contracts_must_be_declared_by_manifest(self):
        list_api = ManagementApiContract("posts.list.v1")
        create_api = ManagementApiContract("posts.create.v1")
        schema = ManagementUiSchema(
            version="1",
            read_contract=list_api.id,
            write_contract=create_api.id,
            sections=(
                ManagementSection(
                    id="posts",
                    title="Posts",
                    fields=(
                        ManagementField(
                            key="posts",
                            label="Posts",
                            type="collection",
                            config_path="posts",
                            collection=ManagementCollectionSchema(
                                operations=ManagementCollectionOperations(
                                    list_contract=list_api.id,
                                    create_contract=create_api.id,
                                    update_contract="posts.update.v1",
                                ),
                                item_fields=(),
                            ),
                        ),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "posts.update.v1"):
            self.manifest(
                management_apis=SkillManagementApis(exposes=(list_api, create_api)),
                management_ui=schema,
            )

    def test_opaque_collection_remains_backward_compatible(self):
        field = ManagementField(
            key="items",
            label="Items",
            type="collection",
            config_path="items",
        )
        self.assertIsNone(field.collection)

    def test_management_ui_rejects_unknown_field_type(self):
        with self.assertRaisesRegex(ValueError, "Unsupported Management field type"):
            ManagementField(
                key="unsafe",
                label="Unsafe",
                type="javascript",
                config_path="settings.unsafe",
            )

    def test_manifest_rejects_management_ui_contract_not_declared(self):
        schema = ManagementUiSchema(
            version="1",
            read_contract="fixture.get-config.v1",
            write_contract="fixture.update-config.v1",
        )
        with self.assertRaisesRegex(ValueError, "undeclared management API"):
            self.manifest(management_ui=schema)

    def test_manifest_accepts_declared_management_ui_contracts(self):
        get_contract = ManagementApiContract("recurring-posts.get-config.v1")
        update_contract = ManagementApiContract("recurring-posts.update-config.v1")
        schema = ManagementUiSchema(
            version="1",
            read_contract=get_contract.id,
            write_contract=update_contract.id,
            sections=(
                ManagementSection(
                    id="general",
                    title="General",
                    fields=(
                        ManagementField(
                            key="enabled",
                            label="Enabled",
                            type="boolean",
                            config_path="enabled",
                        ),
                    ),
                ),
            ),
        )
        manifest = self.manifest(
            management_apis=SkillManagementApis(exposes=(get_contract, update_contract)),
            management_ui=schema,
        )
        self.assertIs(manifest.management_ui, schema)

    def test_duplicate_management_api_is_rejected(self):
        contract = ManagementApiContract("recurring-posts.list.v1")
        with self.assertRaisesRegex(ValueError, "Duplicate management API"):
            self.manifest(
                management_apis=SkillManagementApis(exposes=(contract, contract))
            )

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
        object.__setattr__(manifest, "management_apis", SkillManagementApis())
        validate_manifest(manifest, supported_api_versions=frozenset({"1", "2"}))


if __name__ == "__main__":
    unittest.main()
