from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

FIELD_KEY = re.compile(r"^[a-z][a-z0-9]*(?:[A-Z][a-zA-Z0-9]*)?$|^[a-z][a-z0-9-]*$")
CONFIG_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*$")

FIELD_TYPES = frozenset({
    "boolean",
    "integer",
    "string",
    "long_text",
    "select",
    "multi_select",
    "discord_channel",
    "discord_role",
    "timezone",
    "schedule",
    "collection",
})


@dataclass(frozen=True, slots=True)
class ManagementDocumentBinding:
    """Portable mapping between a document-style form and Management API payloads."""

    read_path: str | None = None
    write_path: str | None = None
    revision_path: str | None = None
    expected_revision_key: str | None = None

    def __post_init__(self) -> None:
        for value in (self.read_path, self.write_path, self.revision_path):
            if value is not None and not CONFIG_PATH.fullmatch(value):
                raise ValueError("Management document paths must be valid config paths.")
        if (
            self.expected_revision_key is not None
            and not FIELD_KEY.fullmatch(self.expected_revision_key)
        ):
            raise ValueError("Management expected revision key must be a stable field key.")


@dataclass(frozen=True, slots=True)
class ManagementFieldOption:
    value: str
    label: str

    def __post_init__(self) -> None:
        if not str(self.value):
            raise ValueError("Management field option value is required.")
        if not str(self.label).strip():
            raise ValueError("Management field option label is required.")


@dataclass(frozen=True, slots=True)
class ManagementCollectionOperations:
    list_contract: str
    create_contract: str
    get_contract: str | None = None
    validate_contract: str | None = None
    update_contract: str | None = None
    set_active_contract: str | None = None
    delete_preview_contract: str | None = None
    delete_contract: str | None = None
    describe_contract: str | None = None

    def __post_init__(self) -> None:
        if not self.list_contract or not self.create_contract:
            raise ValueError("Management collection requires list and create contracts.")

    def contract_ids(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.list_contract,
                self.create_contract,
                self.get_contract,
                self.describe_contract,
                self.validate_contract,
                self.update_contract,
                self.set_active_contract,
                self.delete_preview_contract,
                self.delete_contract,
            )
            if value
        )


@dataclass(frozen=True, slots=True)
class ManagementCollectionSchema:
    operations: ManagementCollectionOperations
    item_fields: tuple["ManagementField", ...]
    item_id_path: str = "id"
    item_id_payload_key: str = "itemId"
    title_path: str = "name"
    status_path: str | None = None
    summary_path: str | None = None
    max_items: int | None = None
    item_read_path: str | None = None

    def __post_init__(self) -> None:
        if not CONFIG_PATH.fullmatch(self.item_id_path):
            raise ValueError("Management collection item_id_path must be a config path.")
        if not FIELD_KEY.fullmatch(self.item_id_payload_key):
            raise ValueError("Management collection item_id_payload_key must be a stable field key.")
        if self.item_read_path is not None and not CONFIG_PATH.fullmatch(self.item_read_path):
            raise ValueError("Management collection item_read_path must be a config path.")
        if not CONFIG_PATH.fullmatch(self.title_path):
            raise ValueError("Management collection title_path must be a config path.")
        for optional in (self.status_path, self.summary_path):
            if optional is not None and not CONFIG_PATH.fullmatch(optional):
                raise ValueError("Management collection display paths must be config paths.")
        if self.max_items is not None and self.max_items <= 0:
            raise ValueError("Management collection max_items must be positive.")
        keys = tuple(field.key for field in self.item_fields)
        if len(keys) != len(set(keys)):
            raise ValueError("Management collection item field keys must be unique.")


@dataclass(frozen=True, slots=True)
class ManagementField:
    key: str
    label: str
    type: str
    config_path: str
    description: str = ""
    required: bool = False
    minimum: int | None = None
    maximum: int | None = None
    options: tuple[ManagementFieldOption, ...] = ()
    collection: ManagementCollectionSchema | None = None

    def __post_init__(self) -> None:
        if not FIELD_KEY.fullmatch(self.key):
            raise ValueError("Management field key must be a stable machine identifier.")
        if not self.label.strip():
            raise ValueError("Management field label is required.")
        if self.type not in FIELD_TYPES:
            raise ValueError(f"Unsupported Management field type: {self.type}.")
        if not CONFIG_PATH.fullmatch(self.config_path):
            raise ValueError("Management field config_path must use dot-separated identifiers.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Management field minimum cannot exceed maximum.")
        if self.type in {"select", "multi_select"} and not self.options:
            raise ValueError("Select Management fields require options.")
        if self.options and self.type not in {"select", "multi_select"}:
            raise ValueError("Only select Management fields may declare options.")
        if self.type != "collection" and self.collection is not None:
            raise ValueError("Only collection Management fields may declare collection operations.")
        values = tuple(option.value for option in self.options)
        if len(values) != len(set(values)):
            raise ValueError("Management field option values must be unique.")


@dataclass(frozen=True, slots=True)
class ManagementSection:
    id: str
    title: str
    description: str = ""
    fields: tuple[ManagementField, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"^[a-z][a-z0-9-]*$", self.id):
            raise ValueError("Management section id must be lowercase kebab-case.")
        if not self.title.strip():
            raise ValueError("Management section title is required.")
        keys = tuple(field.key for field in self.fields)
        if len(keys) != len(set(keys)):
            raise ValueError("Management field keys must be unique within a section.")


@dataclass(frozen=True, slots=True)
class ManagementUiSchema:
    """Declarative, host-neutral management form description.

    Hosts decide how fields are rendered. Skills provide no executable frontend
    code through this contract.
    """

    version: str
    read_contract: str
    write_contract: str
    sections: tuple[ManagementSection, ...] = ()
    document: ManagementDocumentBinding | None = None

    def __post_init__(self) -> None:
        if self.version != "1":
            raise ValueError("Unsupported Management UI schema version.")
        if not self.read_contract or not self.write_contract:
            raise ValueError("Management UI schema requires read and write contracts.")
        ids = tuple(section.id for section in self.sections)
        if len(ids) != len(set(ids)):
            raise ValueError("Management section IDs must be unique.")
        paths = tuple(field.config_path for section in self.sections for field in section.fields)
        if len(paths) != len(set(paths)):
            raise ValueError("Management UI config paths must be unique.")
