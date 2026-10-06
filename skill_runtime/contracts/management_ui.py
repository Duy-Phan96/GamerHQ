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
class ManagementFieldOption:
    value: str
    label: str

    def __post_init__(self) -> None:
        if not str(self.value):
            raise ValueError("Management field option value is required.")
        if not str(self.label).strip():
            raise ValueError("Management field option label is required.")


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
