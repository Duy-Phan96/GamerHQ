"""Reviewed Server Booster lounge using Discord's managed premium role as truth."""
from __future__ import annotations

from dataclasses import dataclass

import discord

from database import db
from services.authorization_service import authorized
from services.onboarding_service import alias, is_staff
from services.server_service import ServerMessageError, upsert_fixed_message

NAME = "booster-lounge"
LABEL = "💎・booster-lounge"
TOPIC = "Private lounge for current Discord Server Boosters and GamerHQ staff."
MESSAGE_KEY_PREFIX = "booster_lounge_message"


@dataclass(frozen=True, slots=True)
class BoosterLoungePlan:
    guild_id: int
    actor_id: int
    action: str
    booster_role_id: int
    category_id: int
    channel_id: int | None
    expected_name: str


def booster_role(guild):
    """Use Discord's premium-subscriber role; never create a duplicate booster role."""
    role = getattr(guild, "premium_subscriber_role", None)
    if role is None:
        return None
    if role not in getattr(guild, "roles", ()):
        return None
    return role


def community_category(guild):
    from services.community_structure_service import core_category
    category = core_category(guild, "community")
    if not isinstance(category, discord.CategoryChannel):
        raise ServerMessageError("COMMUNITY category is unavailable; review Server Structure first.")
    return category


def _mapped(guild):
    raw = db.get_setting(f"managed_channel:{guild.id}:{NAME}")
    return guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None


def _named_candidates(guild, category):
    return [
        channel for channel in guild.text_channels
        if channel.category_id == category.id and alias(channel.name) == NAME
    ]


def lounge(guild):
    category = community_category(guild)
    mapped = _mapped(guild)
    if mapped is None:
        return None
    if mapped not in guild.text_channels or mapped.category_id != category.id:
        return None
    return mapped


def overwrites(guild):
    role = booster_role(guild)
    if role is None:
        raise ValueError(
            "Discord's Server Booster role is unavailable. GamerHQ will not create a duplicate booster role."
        )

    result = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            read_message_history=False,
            send_messages=False,
        ),
        role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            add_reactions=True,
            use_application_commands=True,
        ),
    }
    for candidate in guild.roles:
        if is_staff(candidate):
            result[candidate] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                manage_messages=True,
            )
    if guild.me:
        result[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            manage_messages=True,
            embed_links=True,
        )
    return result


def _access_matches(guild, channel):
    expected = overwrites(guild)
    if set(channel.overwrites) != set(expected):
        return False
    return all(channel.overwrites_for(target).pair() == value.pair() for target, value in expected.items())


def inspect(guild):
    role = booster_role(guild)
    try:
        category = community_category(guild)
    except ServerMessageError:
        return {"status": "BLOCKED", "role": role, "category": None, "channel": None, "reason": "COMMUNITY category unavailable."}

    mapped = _mapped(guild)
    named = _named_candidates(guild, category)

    if mapped is not None and mapped not in guild.text_channels:
        mapped = None
    if mapped is not None and mapped.category_id != category.id:
        return {"status": "BLOCKED", "role": role, "category": category, "channel": mapped, "reason": "Stored Booster Lounge is outside COMMUNITY."}
    if mapped is not None and named and any(channel.id != mapped.id for channel in named):
        return {"status": "BLOCKED", "role": role, "category": category, "channel": mapped, "reason": "Multiple Booster Lounge identities need manual review."}
    if mapped is None and len(named) > 1:
        return {"status": "BLOCKED", "role": role, "category": category, "channel": None, "reason": "Multiple Booster Lounge channels need manual review."}

    channel = mapped or (named[0] if len(named) == 1 else None)
    if role is None:
        return {
            "status": "BLOCKED",
            "role": None,
            "category": category,
            "channel": channel,
            "reason": "Discord's managed Server Booster role is not available yet.",
        }
    if channel is None:
        return {"status": "MISSING", "role": role, "category": category, "channel": None, "reason": "Booster Lounge has not been created."}
    if mapped is None:
        return {"status": "ADOPT", "role": role, "category": category, "channel": channel, "reason": "Existing Booster Lounge can be adopted and secured."}
    if channel.name != LABEL or channel.topic != TOPIC or not _access_matches(guild, channel):
        return {"status": "REPAIR", "role": role, "category": category, "channel": channel, "reason": "Booster Lounge access or metadata needs repair."}
    return {"status": "READY", "role": role, "category": category, "channel": channel, "reason": "Booster Lounge is ready."}


def preview(guild, actor):
    if not authorized(guild, actor):
        raise PermissionError("Administrator access is required.")
    state = inspect(guild)
    if state["status"] == "BLOCKED":
        raise ValueError(state["reason"])
    role, category, channel = state["role"], state["category"], state["channel"]
    return BoosterLoungePlan(
        guild_id=guild.id,
        actor_id=actor.id,
        action={
            "MISSING": "CREATE",
            "ADOPT": "ADOPT",
            "REPAIR": "REPAIR",
            "READY": "VERIFY",
        }[state["status"]],
        booster_role_id=role.id,
        category_id=category.id,
        channel_id=channel.id if channel else None,
        expected_name=channel.name if channel else "",
    )


def render_plan(guild, plan):
    role = guild.get_role(plan.booster_role_id)
    category = guild.get_channel(plan.category_id)
    action = {
        "CREATE": "Create a private Booster Lounge",
        "ADOPT": "Adopt and secure the existing Booster Lounge",
        "REPAIR": "Repair Booster Lounge access and metadata",
        "VERIFY": "Verify the existing Booster Lounge",
    }[plan.action]
    return (
        "# 💎 Review Server Booster Lounge\n"
        f"**Action:** {action}\n"
        f"**Discord-managed role:** {getattr(role, 'mention', '@Server Booster')}\n"
        f"**Category:** {getattr(category, 'mention', 'COMMUNITY')}\n\n"
        "Access will be limited to current Discord Server Boosters, GamerHQ staff and the bot. "
        "GamerHQ will not create a second booster role.\n\n"
        "Existing unrelated member roles are not changed. Nothing happens until you confirm."
    )[:1950]


async def _welcome_message(channel):
    content = (
        "# 💎 Booster Lounge\n\n"
        "Thanks for supporting GamerHQ! This lounge is for current Discord Server Boosters.\n\n"
        "Boosting is appreciated, but never required to participate in the GamerHQ community. "
        "Future booster perks should stay cosmetic/community-focused rather than pay-to-win."
    )
    return await upsert_fixed_message(
        channel,
        setting_key=f"{MESSAGE_KEY_PREFIX}:{channel.guild.id}",
        content=content,
        pin=True,
        allowed_mentions=discord.AllowedMentions.none(),
        recover_match=lambda message: (message.content or "").startswith("# 💎 Booster Lounge"),
    )


async def apply(guild, actor, plan):
    if guild.id != plan.guild_id or actor.id != plan.actor_id or not authorized(guild, actor):
        raise PermissionError("This Booster Lounge review is no longer authorized.")

    role = booster_role(guild)
    category = community_category(guild)
    if role is None or role.id != plan.booster_role_id or category.id != plan.category_id:
        raise ValueError("Booster role or COMMUNITY category changed. Reopen the review.")

    current = inspect(guild)
    channel = current["channel"]
    if plan.channel_id is None:
        if channel is not None:
            raise ValueError("A Booster Lounge appeared while this review was open. Reopen the review.")
        channel = await category.create_text_channel(
            LABEL,
            topic=TOPIC,
            overwrites=overwrites(guild),
            reason="Confirmed GamerHQ Server Booster lounge setup",
        )
    else:
        if channel is None or channel.id != plan.channel_id or channel.name != plan.expected_name:
            raise ValueError("Booster Lounge changed while this review was open. Reopen the review.")
        channel = await channel.edit(
            name=LABEL,
            topic=TOPIC,
            overwrites=overwrites(guild),
            reason="Confirmed GamerHQ Server Booster lounge repair",
        )

    db.set_setting(f"managed_channel:{guild.id}:{NAME}", channel.id)
    await _welcome_message(channel)
    return channel
