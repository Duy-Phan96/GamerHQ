"""Narrow ownership boundary for the Host and Event Manager's shared text parent."""
import json

import discord

from database import db, tenants

BOARDS = frozenset(("create-event", "looking-for-group", "community-events", "tournaments", "giveaways"))


def owned_ids(guild):
    tenants.require_guild(guild.id)
    with db.connect() as conn:
        return {int(row[0]) for row in conn.execute(
            "SELECT json_extract(state_json,'$.channelId') FROM skill_managed_resources WHERE guild_id=?",
            (guild.id,)) if row[0]}


def policies(guild):
    tenants.require_guild(guild.id)
    prefix = f"skill_resources:{guild.id}:"
    with db.connect() as conn:
        rows = conn.execute("SELECT key,value FROM settings WHERE key LIKE ?", (prefix + "%",)).fetchall()
    result = {}
    for row in rows:
        policy = json.loads(row["value"])
        if not isinstance(policy, dict):
            raise ValueError("Invalid Skill resource policy.")
        result[row["key"][len(prefix):]] = policy
    return result


def configured_parents(guild):
    return {int(policy[slot]) for policy in policies(guild).values()
            for slot in ("categoryId", "voiceCategoryId") if policy.get(slot)}


def shared_id(guild, *, owned=None):
    """A parent is shared only with one exact, already reviewed text binding."""
    raw = db.get_setting(f"managed_category:{guild.id}:events")
    if not raw or not raw.isdecimal():
        return None
    ident = int(raw)
    all_policies = policies(guild)
    policy = all_policies.get("event-manager", {})
    if str(policy.get("categoryId")) != raw:
        return None
    if any(str(value.get(slot)) == raw for skill, value in all_policies.items()
           for slot in ("categoryId", "voiceCategoryId")
           if (skill, slot) != ("event-manager", "categoryId")):
        return None
    if ident in (owned if owned is not None else owned_ids(guild)):
        return None
    parent = guild.get_channel(ident)
    if not isinstance(parent, discord.CategoryChannel) or parent.guild.id != guild.id:
        return None
    overwrite = parent.overwrites_for(guild.default_role)
    if overwrite.view_channel is False or overwrite.read_message_history is False:
        return None
    return ident


def is_skill_channel(channel, owned):
    return channel.id in owned or (getattr(channel, "topic", None) or "").startswith("skill-resource:")


def safe_board(guild, name, channel, shared, owned):
    if name not in BOARDS or is_skill_channel(channel, owned):
        return False
    raw = db.get_setting(f"managed_channel:{guild.id}:{name}")
    if raw and raw != str(channel.id):
        return False
    if channel.category_id != shared or channel.guild.id != guild.id:
        return False
    overwrite = channel.overwrites_for(guild.default_role)
    return overwrite.view_channel is not False and overwrite.read_message_history is not False


def blocked_destination(guild, channel, protected, *, shared=None, owned=None):
    """Message destinations need an exact Core mapping, not merely a shared parent."""
    if channel.id in protected or (getattr(channel, "topic", None) or "").startswith("skill-resource:"):
        return True
    if channel.category_id not in protected:
        return False
    if shared is None or channel.category_id != shared:
        return True
    return not any(db.get_setting(f"managed_channel:{guild.id}:{name}") == str(channel.id)
                   and safe_board(guild, name, channel, shared, owned or set()) for name in BOARDS)
