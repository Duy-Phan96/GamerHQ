from __future__ import annotations

from dataclasses import dataclass
import re

API_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[a-z][a-z0-9-]*)*\.v[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class PublicApiContract:
    """Versioned request/response contract exposed by a Skill.

    Prefer Events for "something happened" notifications. Use a Public Skill API
    only when another Skill needs a direct request/response operation.
    """

    id: str
    description: str = ""

    def __post_init__(self) -> None:
        if not API_ID.fullmatch(self.id):
            raise ValueError(
                "Public Skill API IDs must be stable and versioned, e.g. "
                "events.get-event.v1."
            )
