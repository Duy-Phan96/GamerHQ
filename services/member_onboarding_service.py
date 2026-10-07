"""Server-side desired state for GamerHQ member profile and onboarding.

This module owns the human-facing onboarding/profile design. Discord onboarding
remains an integration target; no Discord UI state is treated as authoritative
here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json

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
    enabled: bool = True


CONFIG_SCHEMA_VERSION = 1


def _config_key(guild_id: int) -> str:
    return f"member_onboarding_config:{guild_id}"


def load_config(guild_id: int) -> dict:
    raw = db.get_setting(_config_key(guild_id))
    if not raw:
        return {"schemaVersion": CONFIG_SCHEMA_VERSION, "revision": 0, "questions": {}}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Member onboarding configuration is invalid.") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        raise ValueError("Member onboarding configuration version is unsupported.")
    if not isinstance(value.get("revision", 0), int) or not isinstance(value.get("questions", {}), dict):
        raise ValueError("Member onboarding configuration is invalid.")
    return value


def save_question_override(
    guild_id: int,
    question_key: str,
    *,
    prompt: str | None = None,
    enabled: bool | None = None,
    required: bool | None = None,
    multiple: bool | None = None,
    before_join: bool | None = None,
    expected_revision: int | None = None,
) -> int:
    valid = {question.key for question in default_questions()}
    if question_key not in valid:
        raise ValueError("Unknown onboarding question.")
    config = load_config(guild_id)
    if expected_revision is not None and config["revision"] != expected_revision:
        raise ValueError("Onboarding settings changed. Reopen the question.")
    values = dict(config["questions"].get(question_key, {}))
    if prompt is not None:
        prompt = " ".join(str(prompt).split())
        if not 3 <= len(prompt) <= 100:
            raise ValueError("Question text must be between 3 and 100 characters.")
        values["prompt"] = prompt
    for key, value in (
        ("enabled", enabled),
        ("required", required),
        ("multiple", multiple),
        ("beforeJoin", before_join),
    ):
        if value is not None:
            if not isinstance(value, bool):
                raise ValueError("Question flags must be true or false.")
            values[key] = value
    config["questions"][question_key] = values
    config["revision"] += 1
    db.set_setting(_config_key(guild_id), json.dumps(config, sort_keys=True, separators=(",", ":")))
    return config["revision"]


def reset_config(guild_id: int, *, expected_revision: int | None = None) -> int:
    config = load_config(guild_id)
    if expected_revision is not None and config["revision"] != expected_revision:
        raise ValueError("Onboarding settings changed. Reopen Member Onboarding.")
    revision = config["revision"] + 1
    db.set_setting(
        _config_key(guild_id),
        json.dumps(
            {"schemaVersion": CONFIG_SCHEMA_VERSION, "revision": revision, "questions": {}},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return revision


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


def questions_for_guild(guild, *, include_disabled: bool = False) -> tuple[OnboardingQuestion, ...]:
    config = load_config(guild.id)
    result = []
    for question in default_questions():
        override = config["questions"].get(question.key, {})
        question = replace(
            question,
            prompt=str(override.get("prompt", question.prompt)),
            enabled=bool(override.get("enabled", question.enabled)),
            required=bool(override.get("required", question.required)),
            multiple=bool(override.get("multiple", question.multiple)),
            before_join=bool(override.get("beforeJoin", question.before_join)),
        )
        if not include_disabled and not question.enabled:
            continue
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
                enabled=question.enabled,
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
        "question_revision": load_config(guild.id)["revision"],
    }
