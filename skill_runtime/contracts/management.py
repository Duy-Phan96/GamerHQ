from __future__ import annotations

from dataclasses import dataclass
import re

MANAGEMENT_API_ID = re.compile(
    r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[a-z][a-z0-9-]*)*\.v[1-9][0-9]*$"
)


@dataclass(frozen=True, slots=True)
class ManagementApiContract:
    """Versioned host-to-Skill management operation.

    Management APIs are for trusted host administration surfaces. They are not
    Skill-to-Skill APIs and do not expose host implementation objects.
    """

    id: str
    description: str = ""

    def __post_init__(self) -> None:
        if not MANAGEMENT_API_ID.fullmatch(self.id):
            raise ValueError(
                "Management API IDs must be stable and versioned, e.g. "
                "recurring-posts.list.v1."
            )
