"""Read-only GamerHQ member profile projection over existing authoritative state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import discord

from database import db
from services.role_service import ROLE_GROUPS


PROFILE_ROLE_GROUPS = ("Gender", "Age group", "🖥️ Platform")


@dataclass(frozen=True)
class MemberProfile:
    member_id: int
    display_name: str
    joined_at: datetime | None
    gender: tuple[str, ...]
    age: tuple[str, ...]
    platforms: tuple[str, ...]
    games: tuple[str, ...]
    server_booster: bool


def _member_role_ids(member: discord.Member) -> set[int]:
    return {role.id for role in getattr(member, "roles", ())}


def _managed_profile_values(guild: discord.Guild, member_role_ids: set[int], group: str) -> tuple[str, ...]:
    """Project only explicitly allowlisted, currently mapped profile roles."""
    values: list[str] = []
    for option in ROLE_GROUPS[group]:
        row = db.get_managed_role_by_key("base", option.key)
        if not row:
            continue
        try:
            role_id = int(row["role_id"])
        except (KeyError, TypeError, ValueError):
            continue
        role = guild.get_role(role_id)
        if role is None or role_id not in member_role_ids:
            continue
        values.append(option.label)
    return tuple(values)


def _member_games(guild: discord.Guild, member_role_ids: set[int]) -> tuple[str, ...]:
    """Read game identity from the Game Library; unknown/unmapped roles never leak."""
    games = []
    for game in db.get_selectable_games():
        role_id = game.get("role_id")
        try:
            role_id = int(role_id) if role_id is not None else None
        except (TypeError, ValueError):
            role_id = None
        if role_id is None or guild.get_role(role_id) is None:
            continue
        if role_id in member_role_ids:
            games.append(game["name"])
    return tuple(sorted(games, key=str.casefold))


def project_member_profile(member: discord.Member) -> MemberProfile:
    """Build a profile without persisting any duplicated member state."""
    guild = member.guild
    held = _member_role_ids(member)
    booster_role = getattr(guild, "premium_subscriber_role", None)
    booster = bool(booster_role and booster_role.id in held)

    return MemberProfile(
        member_id=member.id,
        display_name=getattr(member, "display_name", getattr(member, "name", str(member))),
        joined_at=getattr(member, "joined_at", None),
        gender=_managed_profile_values(guild, held, "Gender"),
        age=_managed_profile_values(guild, held, "Age group"),
        platforms=_managed_profile_values(guild, held, "🖥️ Platform"),
        games=_member_games(guild, held),
        server_booster=booster,
    )


def _date_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return discord.utils.format_dt(value, style="D")


def profile_embed(member: discord.Member, profile: MemberProfile | None = None) -> discord.Embed:
    """Render only profile-safe fields. Empty sections are omitted."""
    profile = profile or project_member_profile(member)
    embed = discord.Embed(
        title=f"👤 {profile.display_name}",
        description="GamerHQ Member Profile",
    )

    about = []
    if profile.age:
        about.append(f"**Age:** {', '.join(profile.age)}")
    if profile.gender:
        about.append(f"**Gender:** {', '.join(profile.gender)}")
    if about:
        embed.add_field(name="👤 About You", value="\n".join(about), inline=False)

    gaming = []
    if profile.games:
        gaming.append(f"**Games:** {', '.join(profile.games)}")
    if profile.platforms:
        gaming.append(f"**Platforms:** {', '.join(profile.platforms)}")
    if gaming:
        embed.add_field(name="🎮 Gaming", value="\n".join(gaming), inline=False)

    community = []
    joined = _date_text(profile.joined_at)
    if joined:
        community.append(f"**Member since:** {joined}")
    if profile.server_booster:
        community.append("💎 **Server Booster**")
    if community:
        embed.add_field(name="💎 Community", value="\n".join(community), inline=False)

    if not embed.fields:
        embed.description = (
            "No public GamerHQ profile details are set yet. "
            "Use the profile and game selectors to build your profile."
        )

    avatar = getattr(getattr(member, "display_avatar", None), "url", None)
    if avatar:
        embed.set_thumbnail(url=str(avatar))
    embed.set_footer(text="Profile data is read from existing GamerHQ roles, games and Discord state.")
    return embed
