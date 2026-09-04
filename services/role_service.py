import json
import re
from dataclasses import dataclass

import discord

from database import db


@dataclass(frozen=True)
class RoleOption:
    key: str
    label: str
    emoji: str
    aliases: tuple[str, ...] = ()


ROLE_GROUPS: dict[str, tuple[RoleOption, ...]] = {
    "🖥️ Platform": (
        RoleOption("pc", "PC", "🖥️", ("computer",)),
        RoleOption("playstation", "PlayStation", "🎮", ("ps", "ps5", "ps4")),
        RoleOption("xbox", "Xbox", "🟩"),
        RoleOption("nintendo", "Nintendo", "🔴", ("switch", "nintendo switch")),
        RoleOption("mobile", "Mobile", "📱", ("phone",)),
    ),
    "🎯 Playstyle": (
        RoleOption("competitive", "Competitive", "🏆", ("ranked",)),
        RoleOption("casual", "Casual", "😎"),
    ),
    "🗣️ Language": (
        RoleOption("english", "English", "🇬🇧", ("en",)),
        RoleOption("german", "German", "🇩🇪", ("de", "deutsch")),
    ),
    "🔔 Notifications": (
        RoleOption("lfg-pings", "LFG Pings", "🎮", ("lfg", "looking for group")),
        RoleOption("community-events", "Community Events", "🏆", ("events", "event notifications")),
        RoleOption("stream-updates", "Stream Updates", "🔴", ("stream notifications", "streams")),
        RoleOption("giveaways", "Giveaways", "🎁", ("giveaway notifications",)),
    ),
}


def normalize_role_name(value: str) -> str:
    value = value.casefold().replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _role_terms(option: RoleOption) -> set[str]:
    return {
        normalize_role_name(option.key),
        normalize_role_name(option.label),
        *(normalize_role_name(alias) for alias in option.aliases),
    }


def _expected_base_keys() -> set[str]:
    return {option.key for options in ROLE_GROUPS.values() for option in options}


def _safe_role(role: discord.Role, guild: discord.Guild) -> bool:
    if role.is_default() or role.managed or role.permissions.administrator:
        return False
    me = guild.me
    if me is not None and role >= me.top_role:
        return False
    return True


def _registered_role_for_key(guild: discord.Guild, key: str) -> discord.Role | None:
    row = db.get_managed_role_by_key("base", key)
    if not row:
        return None
    role = guild.get_role(int(row["role_id"]))
    return role


def find_discord_role(guild: discord.Guild, option: RoleOption) -> discord.Role | None:
    # Prefer the role already adopted by GamerHQ so aliases/duplicates never make
    # the bot create a second replacement role.
    registered = _registered_role_for_key(guild, option.key)
    if registered is not None:
        return registered

    terms = _role_terms(option)
    exact = [role for role in guild.roles if normalize_role_name(role.name) in terms]
    if not exact:
        return None

    # Prefer the canonical label when several legacy aliases exist.
    canonical = [role for role in exact if normalize_role_name(role.name) == normalize_role_name(option.label)]
    if len(canonical) == 1:
        return canonical[0]
    if len(exact) == 1:
        return exact[0]
    return None


def configured_group(guild: discord.Guild, group: str):
    configured = []
    missing = []
    for option in ROLE_GROUPS.get(group, ()):
        role = find_discord_role(guild, option)
        if role is None:
            missing.append(option)
        else:
            configured.append((option, role))
    return configured, missing


def all_configured_roles(guild: discord.Guild):
    result = {}
    for group in ROLE_GROUPS:
        configured, _ = configured_group(guild, group)
        result[group] = configured
    return result


async def ensure_base_roles(guild: discord.Guild) -> tuple[list[discord.Role], list[discord.Role]]:
    """Ensure every fixed GamerHQ profile/notification role exists and is adopted.

    Existing canonical roles are reused and registered as bot-managed. Missing
    roles are created. Suggestions never create roles automatically.
    """
    resolved: list[discord.Role] = []
    created: list[discord.Role] = []
    for group, options in ROLE_GROUPS.items():
        for option in options:
            role = find_discord_role(guild, option)
            if role is None:
                role = await guild.create_role(
                    name=option.label,
                    reason="GamerHQ managed base role sync",
                )
                created.append(role)
            db.upsert_managed_role(
                role_id=role.id,
                role_kind="base",
                role_key=option.key,
                role_group=group,
            )
            resolved.append(role)
    return resolved, created


def base_role_status(guild: discord.Guild) -> tuple[list[tuple[str, RoleOption, discord.Role]], list[tuple[str, RoleOption]]]:
    present = []
    missing = []
    for group, options in ROLE_GROUPS.items():
        for option in options:
            role = find_discord_role(guild, option)
            if role is None:
                missing.append((group, option))
            else:
                present.append((group, option, role))
    return present, missing


def _game_alias_terms(game: dict) -> set[str]:
    values = [game.get("name", "")]
    try:
        values.extend(json.loads(game.get("aliases_json") or "[]"))
    except (TypeError, json.JSONDecodeError):
        pass
    return {normalize_role_name(value) for value in values if value}


def cleanup_candidates(guild: discord.Guild) -> list[tuple[discord.Role, str]]:
    """Return safe cleanup candidates, never deleting anything automatically.

    Candidates are limited to GamerHQ-owned base-role leftovers and legacy game
    alias duplicates where the active game already has a different role_id.
    """
    candidates: dict[int, tuple[discord.Role, str]] = {}

    # Base-role leftovers / duplicates.
    expected = _expected_base_keys()
    registered = db.get_managed_roles("base")
    for row in registered:
        role = guild.get_role(int(row["role_id"]))
        if role is None:
            db.delete_managed_role(int(row["role_id"]))
            continue
        if row["role_key"] not in expected and _safe_role(role, guild):
            candidates[role.id] = (role, "obsolete GamerHQ base role")

    for group, options in ROLE_GROUPS.items():
        for option in options:
            primary = find_discord_role(guild, option)
            if primary is None:
                continue
            terms = _role_terms(option)
            for role in guild.roles:
                if role.id == primary.id or not _safe_role(role, guild):
                    continue
                if normalize_role_name(role.name) in terms:
                    candidates[role.id] = (role, f"duplicate/legacy alias of {option.label}")

    # Game role alias leftovers such as "Overwatch" when the managed game is
    # "Overwatch 2" and its actual role_id points at another role.
    for game in db.get_all_games(active_only=False):
        role_id = game.get("role_id")
        if not role_id:
            continue
        terms = _game_alias_terms(game)
        for role in guild.roles:
            if role.id == int(role_id) or not _safe_role(role, guild):
                continue
            if normalize_role_name(role.name) in terms:
                candidates[role.id] = (role, f"legacy alias/duplicate of game {game['name']}")

    return sorted(candidates.values(), key=lambda item: item[0].position, reverse=True)


async def delete_cleanup_candidates(guild: discord.Guild, role_ids: set[int]) -> tuple[list[str], list[str]]:
    deleted: list[str] = []
    failed: list[str] = []
    current = {role.id: (role, reason) for role, reason in cleanup_candidates(guild)}
    for role_id in role_ids:
        entry = current.get(role_id)
        if not entry:
            continue
        role, _ = entry
        try:
            name = role.name
            await role.delete(reason="GamerHQ confirmed managed role cleanup")
            db.delete_managed_role(role_id)
            deleted.append(name)
        except (discord.Forbidden, discord.HTTPException):
            failed.append(role.name)
    return deleted, failed
