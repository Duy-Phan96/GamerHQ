"""Portable Progression & Achievements Skill foundation.

V1 owns configuration and deterministic level math only. Activity adapters and
reward execution are separate slices so the portable Skill never imports GamerHQ
or discord.py internals.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.lifecycle import SkillHealth
from skill_runtime.contracts.management import ManagementApiContract
from skill_runtime.contracts.manifest import SkillManagementApis, SkillManifest


CONFIG_KEY = "config.v1"
MEMBER_KEY_PREFIX = "member.v1:"

STATUS_API = "progression.status.v1"
GET_CONFIG_API = "progression.get-config.v1"
UPDATE_CONFIG_API = "progression.update-config.v1"
PREVIEW_LEVEL_API = "progression.preview-level.v1"

DEFAULT_CONFIG = {
    "levelCurve": {
        "base": 100,
        "linear": 20,
        "quadratic": 2,
        "maxLevel": 100,
    },
    "xpSources": {
        "voice": {
            "enabled": True,
            "xp": 5,
            "windowMinutes": 10,
            "dailyCap": 180,
            "requiresOtherHuman": True,
            "excludeAfk": True,
        },
        "chat": {
            "enabled": True,
            "xp": 3,
            "cooldownMinutes": 5,
            "dailyCap": 120,
        },
        "lfgParticipation": {"enabled": True, "xp": 25},
        "eventHost": {"enabled": True, "xp": 40},
        "communityEvent": {"enabled": True, "xp": 50},
        "tournamentParticipation": {"enabled": True, "xp": 75},
        "tournamentWin": {"enabled": True, "xp": 100},
    },
    "achievements": [
        {
            "id": "server-booster",
            "name": "Server Booster",
            "emoji": "💎",
            "description": "Boost the Discord server.",
            "xp": 250,
            "enabled": True,
            "repeatable": False,
        },
        {
            "id": "first-game",
            "name": "First Game",
            "emoji": "🎮",
            "description": "Add your first game to your GamerHQ profile.",
            "xp": 25,
            "enabled": True,
            "repeatable": False,
        },
        {
            "id": "first-mate",
            "name": "First Mate",
            "emoji": "🤝",
            "description": "Take part in your first completed LFG event.",
            "xp": 50,
            "enabled": True,
            "repeatable": False,
        },
        {
            "id": "event-regular",
            "name": "Event Regular",
            "emoji": "📅",
            "description": "Attend 10 completed events.",
            "xp": 150,
            "enabled": True,
            "repeatable": False,
        },
        {
            "id": "community-host",
            "name": "Community Host",
            "emoji": "🎤",
            "description": "Host 10 completed events.",
            "xp": 300,
            "enabled": True,
            "repeatable": False,
        },
        {
            "id": "voice-rookie",
            "name": "Voice Rookie",
            "emoji": "🎙️",
            "description": "Spend 5 active hours in voice with other members.",
            "xp": 100,
            "enabled": True,
            "repeatable": False,
        },
        {
            "id": "voice-regular",
            "name": "Voice Regular",
            "emoji": "🎙️",
            "description": "Spend 25 active hours in voice with other members.",
            "xp": 250,
            "enabled": True,
            "repeatable": False,
        },
        {
            "id": "voice-veteran",
            "name": "Voice Veteran",
            "emoji": "🎙️",
            "description": "Spend 100 active hours in voice with other members.",
            "xp": 750,
            "enabled": True,
            "repeatable": False,
        },
    ],
    "rewards": [],
}


def xp_to_next(level: int, curve: Mapping[str, Any] | None = None) -> int:
    curve = curve or DEFAULT_CONFIG["levelCurve"]
    if level < 1:
        raise ValueError("level must be at least 1")
    base = int(curve["base"])
    linear = int(curve["linear"])
    quadratic = int(curve["quadratic"])
    value = base + linear * level + quadratic * level * level
    return max(1, value)


def cumulative_xp_for_level(level: int, curve: Mapping[str, Any] | None = None) -> int:
    if level < 1:
        raise ValueError("level must be at least 1")
    return sum(xp_to_next(current, curve) for current in range(1, level))


def level_for_xp(total_xp: int, curve: Mapping[str, Any] | None = None) -> tuple[int, int, int]:
    if total_xp < 0:
        raise ValueError("total_xp must be non-negative")
    curve = curve or DEFAULT_CONFIG["levelCurve"]
    max_level = int(curve.get("maxLevel", 100))
    level = 1
    remaining = int(total_xp)
    while level < max_level:
        needed = xp_to_next(level, curve)
        if remaining < needed:
            return level, remaining, needed
        remaining -= needed
        level += 1
    return max_level, remaining, 0


def _positive_int(value: Any, name: str, *, minimum: int = 1, maximum: int = 1_000_000) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported range")
    return value


def validate_config(value: Mapping[str, Any]) -> dict[str, Any]:
    config = deepcopy(dict(value))
    curve = dict(config.get("levelCurve") or {})
    curve["base"] = _positive_int(curve.get("base", 100), "levelCurve.base")
    curve["linear"] = _positive_int(curve.get("linear", 20), "levelCurve.linear", minimum=0)
    curve["quadratic"] = _positive_int(curve.get("quadratic", 2), "levelCurve.quadratic", minimum=0)
    curve["maxLevel"] = _positive_int(curve.get("maxLevel", 100), "levelCurve.maxLevel", maximum=1000)
    config["levelCurve"] = curve

    sources = dict(config.get("xpSources") or {})
    for key, source in sources.items():
        if not isinstance(source, Mapping):
            raise ValueError(f"xpSources.{key} must be an object")
        item = dict(source)
        item["enabled"] = bool(item.get("enabled", True))
        item["xp"] = _positive_int(item.get("xp", 1), f"xpSources.{key}.xp")
        if "dailyCap" in item:
            item["dailyCap"] = _positive_int(item["dailyCap"], f"xpSources.{key}.dailyCap")
        sources[key] = item
    config["xpSources"] = sources

    achievements = []
    seen = set()
    for raw in config.get("achievements", ()):
        if not isinstance(raw, Mapping):
            raise ValueError("achievement must be an object")
        item = dict(raw)
        achievement_id = str(item.get("id", "")).strip().lower()
        if not achievement_id or achievement_id in seen:
            raise ValueError("achievement IDs must be unique and non-empty")
        seen.add(achievement_id)
        item["id"] = achievement_id
        item["name"] = str(item.get("name", "")).strip()[:80]
        if not item["name"]:
            raise ValueError("achievement name is required")
        item["emoji"] = str(item.get("emoji", ""))[:16]
        item["description"] = str(item.get("description", "")).strip()[:300]
        item["xp"] = _positive_int(item.get("xp", 1), f"achievement {achievement_id} xp")
        item["enabled"] = bool(item.get("enabled", True))
        item["repeatable"] = bool(item.get("repeatable", False))
        achievements.append(item)
    config["achievements"] = achievements

    rewards = []
    reward_ids = set()
    allowed_types = {"role", "badge", "title", "channel_access", "announcement", "xp_bonus"}
    for raw in config.get("rewards", ()):
        if not isinstance(raw, Mapping):
            raise ValueError("reward must be an object")
        item = dict(raw)
        reward_id = str(item.get("id", "")).strip().lower()
        if not reward_id or reward_id in reward_ids:
            raise ValueError("reward IDs must be unique and non-empty")
        reward_ids.add(reward_id)
        trigger = dict(item.get("trigger") or {})
        trigger_type = str(trigger.get("type", ""))
        if trigger_type not in {"level", "achievement", "xp"}:
            raise ValueError(f"reward {reward_id} has an unsupported trigger")
        if trigger_type == "level":
            trigger["level"] = _positive_int(trigger.get("level"), f"reward {reward_id} level", maximum=curve["maxLevel"])
        elif trigger_type == "xp":
            trigger["xp"] = _positive_int(trigger.get("xp"), f"reward {reward_id} xp")
        else:
            achievement_id = str(trigger.get("achievementId", "")).strip().lower()
            if achievement_id not in seen:
                raise ValueError(f"reward {reward_id} references an unknown achievement")
            trigger["achievementId"] = achievement_id
        item["trigger"] = trigger

        grants = []
        for grant in item.get("grants", ()):
            if not isinstance(grant, Mapping):
                raise ValueError(f"reward {reward_id} grant must be an object")
            grant = dict(grant)
            kind = str(grant.get("type", ""))
            if kind not in allowed_types:
                raise ValueError(f"reward {reward_id} has unsupported grant type")
            grants.append(grant)
        if not grants:
            raise ValueError(f"reward {reward_id} needs at least one grant")
        item["grants"] = grants
        item["enabled"] = bool(item.get("enabled", True))
        item["name"] = str(item.get("name", reward_id)).strip()[:80]
        rewards.append(item)
    config["rewards"] = rewards
    return config


@dataclass(slots=True)
class ProgressionSkill:
    manifest = SkillManifest(
        id="progression",
        name="Progression & Achievements",
        version="0.1.0",
        runtime_api_version="1",
        description="Configurable XP, levels, achievements and rewards for GamerHQ communities.",
        author="GamerHQ",
        permissions=(SkillCapability.STORAGE_SKILL.value,),
        management_apis=SkillManagementApis(
            exposes=(
                ManagementApiContract(STATUS_API),
                ManagementApiContract(GET_CONFIG_API),
                ManagementApiContract(UPDATE_CONFIG_API),
                ManagementApiContract(PREVIEW_LEVEL_API),
            )
        ),
    )

    async def register(self, ctx) -> None:
        ctx.management.expose(STATUS_API, self.status)
        ctx.management.expose(GET_CONFIG_API, self.get_config)
        ctx.management.expose(UPDATE_CONFIG_API, self.update_config)
        ctx.management.expose(PREVIEW_LEVEL_API, self.preview_level)

    async def enable(self, ctx) -> None:
        if await ctx.storage.get(CONFIG_KEY) is None:
            await ctx.storage.set(CONFIG_KEY, deepcopy(DEFAULT_CONFIG))

    async def disable(self, ctx) -> None:
        return None

    async def start(self, ctx) -> None:
        return None

    async def stop(self, ctx) -> None:
        return None

    async def health_check(self, ctx) -> SkillHealth:
        try:
            config = await self._config(ctx)
            validate_config(config)
        except Exception:
            return SkillHealth("FAIL", "Progression configuration is invalid.")
        return SkillHealth("PASS", "Progression configuration is valid.")

    async def _config(self, ctx) -> dict[str, Any]:
        stored = await ctx.storage.get(CONFIG_KEY)
        return validate_config(stored if stored is not None else DEFAULT_CONFIG)

    async def status(self, ctx, payload) -> Mapping[str, Any]:
        config = await self._config(ctx)
        return {
            "version": self.manifest.version,
            "maxLevel": config["levelCurve"]["maxLevel"],
            "xpSourceCount": sum(1 for source in config["xpSources"].values() if source.get("enabled")),
            "achievementCount": sum(1 for achievement in config["achievements"] if achievement.get("enabled")),
            "rewardCount": sum(1 for reward in config["rewards"] if reward.get("enabled")),
        }

    async def get_config(self, ctx, payload) -> Mapping[str, Any]:
        return {"config": await self._config(ctx)}

    async def update_config(self, ctx, payload) -> Mapping[str, Any]:
        if "config" not in payload:
            raise ValueError("config is required")
        config = validate_config(payload["config"])
        await ctx.storage.set(CONFIG_KEY, config)
        await ctx.audit.write(action="progression.config.updated", target="progression")
        return {"config": config}

    async def preview_level(self, ctx, payload) -> Mapping[str, Any]:
        total_xp = _positive_int(payload.get("totalXp", 0), "totalXp", minimum=0)
        config = await self._config(ctx)
        level, current, needed = level_for_xp(total_xp, config["levelCurve"])
        return {
            "totalXp": total_xp,
            "level": level,
            "currentLevelXp": current,
            "xpToNextLevel": needed,
            "progress": 1.0 if needed == 0 else current / needed,
        }


def create_skill():
    return ProgressionSkill()
