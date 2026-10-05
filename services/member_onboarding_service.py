"""Server-side desired state for GamerHQ member profile and onboarding.

This module owns the human-facing onboarding/profile design. Discord onboarding
remains an integration target; no Discord UI state is treated as authoritative
here.
"""
from __future__ import annotations

from dataclasses import dataclass

from database import db
from services import role_service as roles


@dataclass(frozen=True, slots=True)
class OnboardingAnswer:
    key: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class OnboardingQuestion:
    key: str
    prompt: str
    answers: tuple[OnboardingAnswer, ...]
    required: bool = False
    multiple: bool = False
    before_join: bool = True


def _answers(group: str) -> tuple[OnboardingAnswer, ...]:
    return tuple(
        OnboardingAnswer(option.key, f"{option.emoji} {option.label}")
        for option in roles.ROLE_GROUPS[group]
    )


def default_questions() -> tuple[OnboardingQuestion, ...]:
    """Canonical GamerHQ onboarding defaults.

    Keep pre-join questions short. The full game catalog remains available in
    GamerHQ's personal game selector after joining.
    """
    return (
        OnboardingQuestion(
            key="age",
            prompt="What's your age group?",
            answers=_answers("Age group"),
            required=False,
            multiple=False,
            before_join=True,
        ),
        OnboardingQuestion(
            key="gender",
            prompt="How would you like to describe yourself?",
            answers=(
                *_answers("Gender"),
                OnboardingAnswer("gender-unspecified", "⚪ Prefer not to say"),
            ),
            required=False,
            multiple=False,
            before_join=False,
        ),
        OnboardingQuestion(
            key="games",
            prompt="What games do you play?",
            answers=(),
            required=False,
            multiple=True,
            before_join=True,
        ),
        OnboardingQuestion(
            key="community",
            prompt="What would you like to hear about?",
            answers=tuple(
                OnboardingAnswer(option.key, f"{option.emoji} {option.label}")
                for group in ("🔔 Notifications", "📰 Gaming Content")
                for option in roles.ROLE_GROUPS[group]
            ),
            required=False,
            multiple=True,
            before_join=False,
        ),
    )


def popular_games(guild, *, limit: int = 8) -> tuple[dict, ...]:
    """Return a bounded onboarding subset; the full selector remains authoritative."""
    games = []
    for game in db.get_selectable_games():
        role = guild.get_role(int(game["role_id"])) if game.get("role_id") else None
        count = len(getattr(role, "members", ())) if role else 0
        games.append((count, str(game["name"]).casefold(), game))
    games.sort(key=lambda row: (-row[0], row[1]))
    return tuple(row[2] for row in games[:limit])


def questions_for_guild(guild) -> tuple[OnboardingQuestion, ...]:
    result = []
    for question in default_questions():
        if question.key != "games":
            result.append(question)
            continue
        games = popular_games(guild)
        answers = tuple(
            OnboardingAnswer(
                f"game:{game['id']}",
                f"{game.get('emoji') or '🎮'} {game['name']}",
            )
            for game in games
        )
        if games:
            answers += (
                OnboardingAnswer(
                    "games:more",
                    "➕ More Games",
                    "Choose from the full GamerHQ game library after joining.",
                ),
            )
        result.append(
            OnboardingQuestion(
                key=question.key,
                prompt=question.prompt,
                answers=answers,
                required=question.required,
                multiple=question.multiple,
                before_join=question.before_join,
            )
        )
    return tuple(result)


def legacy_profile_mappings() -> tuple[str, ...]:
    keys = (*roles.LEGACY_LANGUAGES, *roles.LEGACY_AGE_KEYS)
    return tuple(
        key for key in keys
        if db.get_managed_role_by_key("base", key) is not None
    )


def profile_status(guild) -> dict:
    present, missing = roles.base_role_status(guild)
    return {
        "present": tuple(present),
        "missing": tuple(missing),
        "legacy": legacy_profile_mappings(),
        "questions": questions_for_guild(guild),
    }
