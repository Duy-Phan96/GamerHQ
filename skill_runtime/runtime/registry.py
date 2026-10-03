from __future__ import annotations

from collections.abc import Iterable

from ..contracts.lifecycle import Skill
from ..contracts.manifest import SUPPORTED_RUNTIME_API_VERSIONS, validate_manifest


class SkillRegistry:
    """Process-local registry of validated Skill implementations.

    Registration is host-neutral. Installation/enabled state is deliberately not
    stored here because that belongs to the host/runtime state store per guild.
    """

    def __init__(self, *, supported_api_versions: frozenset[str] = SUPPORTED_RUNTIME_API_VERSIONS):
        self._supported_api_versions = supported_api_versions
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        validate_manifest(skill.manifest, supported_api_versions=self._supported_api_versions)
        skill_id = skill.manifest.id
        if skill_id in self._skills:
            raise ValueError(f"Skill already registered: {skill_id}.")
        self._skills[skill_id] = skill

    def get(self, skill_id: str) -> Skill:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise KeyError(f"Skill is not registered: {skill_id}.") from exc

    def contains(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def all(self) -> tuple[Skill, ...]:
        return tuple(self._skills[key] for key in sorted(self._skills))

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._skills))

    def extend(self, skills: Iterable[Skill]) -> None:
        for skill in skills:
            self.register(skill)
