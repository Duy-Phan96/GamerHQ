"""Optional Server Booster experience backed by Discord's native booster role."""
from __future__ import annotations

import discord

from database import db
from services.community_structure_service import core_category
from services.onboarding_service import alias, is_staff
from services.server_service import ServerMessageError

CHANNEL_KEY = "booster-lounge"
CHANNEL_NAME = "💎・booster-lounge"
CHANNEL_TOPIC = "Private lounge for current GamerHQ Server Boosters."


def native_booster_role(guild: discord.Guild):
    """Discord owns booster membership; GamerHQ never creates a duplicate role."""
    role = getattr(guild, "premium_subscriber_role", None)
    if role is None:
        raise ValueError(
            "Discord's native Server Booster role is unavailable. "
            "GamerHQ will not create a replacement role."
        )
    return role


def _mapping_key(guild: discord.Guild) -> str:
    return f"managed_channel:{guild.id}:{CHANNEL_KEY}"


def lounge_channel(guild: discord.Guild):
    raw = db.get_setting(_mapping_key(guild))
    mapped = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if isinstance(mapped, discord.TextChannel):
        return mapped

    community = core_category(guild, "community")
    if community is None:
        return None

    matches = [
        channel for channel in community.text_channels
        if alias(channel.name) == CHANNEL_KEY
    ]
    if len(matches) > 1:
        raise ServerMessageError(
            "Multiple booster-lounge channels exist in COMMUNITY; review manually."
        )
    elsewhere = [
        channel for channel in guild.text_channels
        if alias(channel.name) == CHANNEL_KEY and channel not in matches
    ]
    if elsewhere:
        raise ServerMessageError(
            "A booster-lounge exists outside COMMUNITY; review it before setup."
        )
    return matches[0] if matches else None


def desired_overwrites(guild: discord.Guild, booster_role) -> dict:
    """Private allowlist: @everyone denied, booster/staff/bot allowed."""
    result = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            read_message_history=False,
            send_messages=False,
        ),
        booster_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            add_reactions=True,
            embed_links=True,
            attach_files=True,
            use_application_commands=True,
        ),
    }
    for role in guild.roles:
        if is_staff(role):
            result[role] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                manage_messages=True,
                add_reactions=True,
                embed_links=True,
                attach_files=True,
                use_application_commands=True,
            )
    if guild.me:
        result[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            manage_messages=True,
            manage_channels=True,
            add_reactions=True,
            embed_links=True,
            attach_files=True,
            use_application_commands=True,
        )
    return result


def status(guild: discord.Guild) -> dict:
    role = native_booster_role(guild)
    community = core_category(guild, "community")
    if community is None:
        raise ServerMessageError(
            "COMMUNITY category is unavailable. Repair the core server structure first."
        )
    channel = lounge_channel(guild)
    desired = desired_overwrites(guild, role)
    mapped = db.get_setting(_mapping_key(guild))
    if channel is None:
        action = "create"
    elif str(mapped or "") != str(channel.id):
        action = "adopt"
    elif (
        channel.category_id != community.id
        or channel.name != CHANNEL_NAME
        or channel.topic != CHANNEL_TOPIC
        or channel.overwrites != desired
    ):
        action = "repair"
    else:
        action = "ready"
    return {
        "role": role,
        "community": community,
        "channel": channel,
        "desired_overwrites": desired,
        "action": action,
    }


def review_snapshot(guild: discord.Guild) -> dict:
    current = status(guild)
    return {
        "role_id": current["role"].id,
        "community_id": current["community"].id,
        "channel_id": current["channel"].id if current["channel"] else None,
        "action": current["action"],
    }


async def apply(guild: discord.Guild, expected: dict):
    """Apply only after rechecking the previewed native role/resource identity."""
    current = status(guild)
    snapshot = {
        "role_id": current["role"].id,
        "community_id": current["community"].id,
        "channel_id": current["channel"].id if current["channel"] else None,
        "action": current["action"],
    }
    if snapshot != expected:
        raise ValueError(
            "Booster setup changed while this review was open. Reopen Server Boosters."
        )

    if current["action"] == "ready":
        return current["channel"], False

    channel = current["channel"]
    if channel is None:
        channel = await current["community"].create_text_channel(
            CHANNEL_NAME,
            topic=CHANNEL_TOPIC,
            overwrites=current["desired_overwrites"],
            reason="Confirmed GamerHQ Server Booster lounge setup",
        )
    else:
        channel = await channel.edit(
            name=CHANNEL_NAME,
            category=current["community"],
            topic=CHANNEL_TOPIC,
            overwrites=current["desired_overwrites"],
            sync_permissions=False,
            reason="Confirmed GamerHQ Server Booster lounge repair",
        )

    db.set_setting(_mapping_key(guild), channel.id)
    return channel, True
