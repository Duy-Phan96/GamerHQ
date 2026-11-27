"""Host-owned public LFG projection; the Event Manager remains the domain authority.

One Discord message per event, showing its earliest open occurrence. Only verified
Host-authored cards may be edited/deleted. A pending send is never blindly retried.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import time

import discord

from database import db, tenants
from hosts.gamerhq.event_cutover import owns_events

SKILL = "event-manager"
_LOCKS = {}


def guild_lock(guild_id):
    return _LOCKS.setdefault(guild_id, asyncio.Lock())


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def card_token(guild_id, event_id):
    return _digest([guild_id, event_id])[:32]


def prefix(guild_id):
    return f"public_lfg_card:{guild_id}:"


def key(guild_id, event_id):
    return prefix(guild_id) + card_token(guild_id, event_id)


def load(guild_id, event_id):
    tenants.require_guild(guild_id)
    raw = db.get_setting(key(guild_id, event_id))
    return json.loads(raw) if raw else None


def records(guild_id):
    tenants.require_guild(guild_id)
    with db.connect() as conn:
        rows = conn.execute("SELECT key,value FROM settings WHERE key LIKE ? AND value != ''",
                            (prefix(guild_id) + "%",)).fetchall()
    result = {}
    for row in rows:
        state = json.loads(row["value"])
        event_id = state["eventId"]
        if row["key"] != key(guild_id, event_id):
            raise ValueError("Public LFG card identity is inconsistent.")
        result[event_id] = state
    return result


def save(guild_id, event_id, state):
    tenants.require_guild(guild_id)
    db.set_setting(key(guild_id, event_id), json.dumps(state, sort_keys=True) if state else "")


@dataclass(frozen=True)
class PublicCard:
    event_id: str
    title: str
    game: str
    description: str
    owner_id: str
    start_at: int
    registered: int
    capacity: int
    recurring: bool


def project(snapshot, *, now=None):
    """Never return private, paused, deleted or expired occurrences."""
    now = time.time() if now is None else now
    events, occurrences = snapshot["events"], snapshot["occurrences"]
    if not isinstance(events, list) or not isinstance(occurrences, list):
        raise ValueError("Invalid Event Manager list response.")
    upcoming = {}
    for row in occurrences:
        if row["status"] in ("scheduled", "active"):
            upcoming.setdefault(row["eventId"], []).append(row)
    cards = {}
    seen = set()
    for event in events:
        event_id = event["id"]
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise ValueError("Invalid or duplicate event identity.")
        seen.add(event_id)
        config = event["config"]
        if event["status"] != "active" or config["visibility"] != "public":
            continue
        sessions = [row for row in upcoming.get(event_id, ())
                    if row["startAt"] + row.get("configSnapshot", config)["durationMinutes"] * 60 > now]
        if not sessions:
            continue
        row = min(sessions, key=lambda value: value["startAt"])
        capacity = config["maxPlayers"]
        if type(capacity) is not int or capacity < 1 or type(row["startAt"]) is not int:
            raise ValueError("Invalid public event capacity or occurrence.")
        cards[event_id] = PublicCard(event_id, str(config["name"])[:100], str(config.get("game") or "")[:100],
            str(config.get("description") or "")[:500], str(event.get("ownerId") or ""),
            row["startAt"], len(set(row["participants"])), capacity, config.get("kind") != "once")
    return cards


def marker(guild_id, event_id):
    return "GamerHQ LFG • " + card_token(guild_id, event_id)


def controls(guild_id, card):
    token = card_token(guild_id, card.event_id)
    view = discord.ui.View(timeout=None)
    for action, label, style in (
        ("join", "Join", discord.ButtonStyle.success),
        ("leave", "Leave", discord.ButtonStyle.secondary),
        ("details", "Details", discord.ButtonStyle.secondary),
        ("share", "Share", discord.ButtonStyle.secondary),
    ):
        view.add_item(discord.ui.Button(label=label, style=style,
            custom_id=f"gamerhq:lfg-card:{action}:{token}",
            disabled=action == "join" and card.registered >= card.capacity))
    return view


def _safe(value):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


def render(guild_id, card):
    embed = discord.Embed(title=_safe(card.title), description=_safe(card.description) or None)
    if card.game:
        embed.add_field(name="Game", value=_safe(card.game))
    if card.owner_id.isdecimal() and len(card.owner_id) <= 20:
        embed.add_field(name="Organizer", value=f"<@{card.owner_id}>")
    embed.add_field(name="Date", value=f"<t:{card.start_at}:F>", inline=False)
    embed.add_field(name="Players", value=f"{card.registered} / {card.capacity}")
    embed.add_field(name="Schedule", value="Recurring" if card.recurring else "One-time")
    embed.set_footer(text=marker(guild_id, card.event_id))
    return embed


def owned_message(guild, message, event_id):
    return (guild.me is not None and message.author.id == guild.me.id and
            any(getattr(embed.footer, "text", None) == marker(guild.id, event_id)
                for embed in message.embeds))


def public_allowed(guild_id):
    try:
        policy = json.loads(db.get_setting(f"skill_resources:{guild_id}:{SKILL}") or "{}")
        return isinstance(policy, dict) and policy.get("allowPublic") is True
    except (TypeError, ValueError):
        return False


async def destination(guild):
    raw = db.get_setting(f"managed_channel:{guild.id}:looking-for-group")
    if not raw or not raw.isdecimal():
        return None
    channel = await guild.fetch_channel(int(raw))
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        raise ValueError("Public LFG destination identity could not be verified.")
    with db.connect() as conn:
        skill_owned = conn.execute("SELECT 1 FROM skill_managed_resources WHERE guild_id=? "
            "AND json_extract(state_json,'$.channelId')=? LIMIT 1", (guild.id, channel.id)).fetchone()
    if skill_owned or (channel.topic or "").startswith("skill-resource:"):
        raise ValueError("Public LFG destination is Skill-owned.")
    if channel.nsfw or guild.me is None or channel.category_id is None:
        raise ValueError("Public LFG destination is not a verified public board.")
    parent = await guild.fetch_channel(channel.category_id)
    if not isinstance(parent, discord.CategoryChannel) or parent.guild.id != guild.id:
        raise ValueError("Public LFG parent identity could not be verified.")
    everyone = guild.default_role
    parent_rights = parent.overwrites_for(everyone)
    if parent_rights.view_channel is False or parent_rights.read_message_history is False:
        raise ValueError("Public LFG parent restricts public access.")
    rights = channel.permissions_for(everyone)
    bot_rights = channel.permissions_for(guild.me)
    if not (rights.view_channel and rights.read_message_history and bot_rights.view_channel
            and bot_rights.read_message_history and bot_rights.send_messages and bot_rights.embed_links):
        raise ValueError("Public LFG board permissions require review.")
    return channel


async def _message(guild, channel, event_id, state):
    if state.get("messageId"):
        try:
            message = await channel.fetch_message(state["messageId"])
        except discord.NotFound:
            return None
        if not owned_message(guild, message, event_id):
            raise ValueError("Stored public LFG message is not verified as Host-owned.")
        return message
    if state.get("pending"):
        matches = [message async for message in channel.history(limit=100)
                   if owned_message(guild, message, event_id)]
        if len(matches) != 1:
            raise ValueError("Public LFG publication outcome is uncertain; manual review required.")
        return matches[0]
    return None


async def retire(guild, event_id, state):
    """Remove only our verified card, never the event chat or member content."""
    try:
        channel = await guild.fetch_channel(state["channelId"])
    except discord.NotFound:
        save(guild.id, event_id, None)
        return
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        raise ValueError("Stored public LFG channel identity is invalid.")
    message = await _message(guild, channel, event_id, state)
    if message is not None:
        try:
            await message.delete()
        except discord.NotFound:
            pass
    save(guild.id, event_id, None)


async def publish(guild, channel, card, state=None):
    event_id = card.event_id
    if state and state["channelId"] != channel.id:
        await retire(guild, event_id, state)
        state = None
    state = state or {"eventId": event_id, "channelId": channel.id}
    message = await _message(guild, channel, event_id, state)
    fingerprint = _digest(asdict(card))
    if message is None:
        state.update(messageId=None, pending=True, digest=None)
        save(guild.id, event_id, state)
        message = await channel.send(embed=render(guild.id, card), view=controls(guild.id, card),
                                     allowed_mentions=discord.AllowedMentions.none())
    elif state.get("digest") != fingerprint or state.get("pending"):
        await message.edit(embed=render(guild.id, card), view=controls(guild.id, card),
                           allowed_mentions=discord.AllowedMentions.none())
    updated = {"eventId": event_id, "channelId": channel.id, "messageId": message.id,
               "pending": False, "digest": fingerprint, "startAt": card.start_at}
    if updated != state:
        save(guild.id, event_id, updated)


async def sync(runtime, guild):
    """Read the authoritative Skill snapshot, then converge the public projection."""
    async with guild_lock(guild.id):
        known = records(guild.id)
        enabled = (runtime.registry.contains(SKILL) and owns_events(guild.id)
                   and await runtime.state.is_enabled(guild_id=guild.id, skill_id=SKILL)
                   and runtime.manager.is_running(guild_id=guild.id, skill_id=SKILL)
                   and public_allowed(guild.id))
        if not enabled:
            for event_id, state in known.items():
                await retire(guild, event_id, state)
            return
        snapshot = await runtime.call_management(guild_id=guild.id, skill_id=SKILL,
            contract_id="event-manager.list.v1", payload={})
        desired = project(snapshot)
        for event_id in known.keys() - desired.keys():
            await retire(guild, event_id, known[event_id])
        channel = await destination(guild)
        if channel is None:
            return
        for event_id, card in desired.items():
            await publish(guild, channel, card, known.get(event_id))


async def before_private_update(runtime, guild_id, payload):
    """Called under guild_lock before the portable API can commit private visibility.

    Failure to remove a public card blocks the visibility change rather than leaving
    its now-private details published. Invalid/rejected updates can be republished
    from the still-public authoritative snapshot on the next pass.
    """
    event_id = payload.get("eventId")
    if payload.get("visibility") != "public" and isinstance(event_id, str):
        state = load(guild_id, event_id)
        if state:
            await retire(runtime._guild(guild_id), event_id, state)


def resolve_control(guild, channel, message, token):
    if (not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id
            or db.get_setting(f"managed_channel:{guild.id}:looking-for-group") != str(channel.id)):
        return None
    for event_id, state in records(guild.id).items():
        if (card_token(guild.id, event_id) == token and state.get("channelId") == channel.id
                and state.get("messageId") == message.id and not state.get("pending")
                and owned_message(guild, message, event_id)):
            return event_id, state["startAt"]
    return None
