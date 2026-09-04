from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from database import db

SERVER_TZ = ZoneInfo("Europe/Berlin")


def discord_timestamp(epoch: int, style: str = "F") -> str:
    return f"<t:{int(epoch)}:{style}>"


def parse_server_datetime(date_text: str, time_text: str) -> int:
    """Parse the first LFG version in GamerHQ server time (Europe/Berlin)."""
    value = f"{date_text.strip()} {time_text.strip()}"
    try:
        local_dt = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=SERVER_TZ)
    except ValueError as exc:
        raise ValueError("Use date `YYYY-MM-DD` and time `HH:MM`, e.g. `2026-08-28` and `20:00`.") from exc
    if local_dt.timestamp() <= datetime.now(tz=SERVER_TZ).timestamp():
        raise ValueError("The event start must be in the future.")
    return int(local_dt.timestamp())


def find_lfg_channel(guild: discord.Guild) -> discord.TextChannel | None:
    import re

    def alias(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    for channel in guild.text_channels:
        if alias(channel.name) in {"looking-for-group", "lfg"}:
            return channel
    return None



def find_game_lfg_channel(guild: discord.Guild, game: dict) -> discord.TextChannel | None:
    # Backward compatibility: the database column is still named clips_channel_id,
    # but from LFG V2 onward it stores the per-game looking-for-group channel.
    cid = game.get("lfg_channel_id") or game.get("clips_channel_id")
    if cid:
        channel = guild.get_channel(int(cid))
        if isinstance(channel, discord.TextChannel):
            return channel
    category_id = game.get("category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if isinstance(category, discord.CategoryChannel):
        for channel in category.text_channels:
            if "looking-for-group" in channel.name or channel.name.endswith("lfg"):
                return channel
    return None

def user_games(member: discord.Member) -> list[dict]:
    role_ids = {role.id for role in member.roles}
    return [
        game for game in db.get_area_games(lfg_only=True)
        if game.get("role_id") and int(game["role_id"]) in role_ids
    ]


def member_has_game_role(member: discord.Member, game: dict) -> bool:
    role_id = game.get("role_id")
    return bool(role_id and member.get_role(int(role_id)))


def event_members(event_id: int) -> list[dict]:
    return db.get_lfg_event_members(event_id)


def joined_user_ids(event_id: int) -> list[int]:
    return [int(row["user_id"]) for row in event_members(event_id) if row["status"] == "joined"]


def excluded_user_ids(event_id: int) -> set[int]:
    return {int(row["user_id"]) for row in event_members(event_id) if row["status"] == "excluded"}


def invited_user_ids(event_id: int) -> set[int]:
    return {int(row["user_id"]) for row in event_members(event_id) if row["status"] == "invited"}


def render_event(guild: discord.Guild, event: dict) -> str:
    game = db.get_game_by_id(int(event["game_id"]))
    game_label = f"{game['emoji']} **{game['name']}**" if game else "🎮 **Unknown Game**"
    joined = joined_user_ids(int(event["id"]))
    host = guild.get_member(int(event["host_id"]))
    host_label = host.mention if host else f"<@{event['host_id']}>"

    lines = [
        f"# 🎮 {event['title']}",
        "",
        game_label,
        f"📅 {discord_timestamp(int(event['start_at']), 'F')} ({discord_timestamp(int(event['start_at']), 'R')})",
        f"👥 **{len(joined)}/{event['max_players']} players**",
        f"🔔 Voice invite: **{event['invite_lead_minutes']} min before**",
        f"👤 Hosted by {host_label}",
        "",
    ]

    if joined:
        mentions = [f"<@{user_id}>" for user_id in joined]
        lines.append("**Players:** " + " · ".join(mentions))
        lines.append("")

    lines.append("Use the buttons below to join or leave this event.")
    return "\n".join(lines)


async def notify_invited_users(guild: discord.Guild, event: dict, message: discord.Message) -> None:
    game = db.get_game_by_id(int(event["game_id"]))
    game_name = game["name"] if game else "Gaming event"
    for user_id in invited_user_ids(int(event["id"])):
        member = guild.get_member(user_id)
        if member is None or member.bot:
            continue
        try:
            await member.send(
                f"# 🎮 GamerHQ Event Invite\n\n"
                f"You've been invited to **{event['title']}** for **{game_name}**.\n"
                f"📅 {discord_timestamp(int(event['start_at']), 'F')}\n\n"
                f"Open the event and use **Join Event** if you'd like to take part:\n{message.jump_url}"
            )
        except (discord.Forbidden, discord.HTTPException):
            # DMs are best-effort. The event itself is still valid.
            continue
