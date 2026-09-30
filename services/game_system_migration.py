"""Explicit batch migration from legacy per-game categories into shared GAMES."""
from __future__ import annotations

import time

import discord

from database import db
from services.authorization_service import authorized
from services import game_channel_service as channels
from services.server_operations import wire


def preview(guild, actor):
    if not authorized(guild, actor):
        raise ValueError("Administrator access is required.")
    parent = channels.category(guild)
    plans, review = [], []
    for game in db.get_all_games():
        if game.get("channel_id"):
            continue
        hints = channels.legacy_hints(game)
        if not hints.get("category_id") or not hints.get("chat_channel_id"):
            continue
        try:
            plans.append(channels.preview(guild, actor, game["id"], "migrate"))
        except ValueError as exc:
            review.append(f"{game['name']}: {exc}")
    return {
        "guild_id": guild.id,
        "actor_id": actor.id,
        "created": time.time(),
        "parent_id": parent.id,
        "parent_wire": wire(parent),
        "plans": plans,
        "review": review,
    }


def render(draft):
    lines = [
        "# 🎮 Game System Migration",
        "",
        f"Ready to migrate: **{len(draft['plans'])}** game chats",
        f"Needs review: **{len(draft['review'])}**",
        "",
    ]
    for plan in draft["plans"][:15]:
        lines.append(f"• {plan['game']['name']} → GAMES/#{channels.slug(plan['game']['name'])}")
    if len(draft["plans"]) > 15:
        lines.append(f"• … {len(draft['plans']) - 15} more")
    for warning in draft["review"][:5]:
        lines.append(f"⚠️ {warning[:160]}")
    lines.extend([
        "",
        "Migration preserves the existing chat ID/history and role mapping. "
        "Legacy per-game LFG/create-voice/category cleanup is a separate confirmed step.",
    ])
    return "\n".join(lines)[:1950]


async def apply(guild, actor, draft):
    if not authorized(guild, actor) or actor.id != draft["actor_id"] or guild.id != draft["guild_id"]:
        raise ValueError("This migration preview belongs to another administrator/server.")
    if time.time() - draft["created"] > 240:
        raise ValueError("Migration preview expired. Open a fresh preview.")
    parent = await guild.fetch_channel(draft["parent_id"])
    if not isinstance(parent, discord.CategoryChannel) or wire(parent) != draft["parent_wire"]:
        raise ValueError("The GAMES destination changed. Open a fresh preview.")

    fresh = []
    for plan in draft["plans"]:
        current = channels.preview(guild, actor, plan["game"]["id"], "migrate")
        for key in ("game", "channel", "signature", "parent", "parent_signature", "role", "hints"):
            if current[key] != plan[key]:
                raise ValueError(f"{plan['game']['name']} changed. Open a fresh migration preview.")
        fresh.append(current)

    moved, skipped = [], []
    for plan in fresh:
        try:
            await channels.apply(guild, actor, plan)
            moved.append(plan["game"]["name"])
        except (ValueError, discord.HTTPException) as exc:
            skipped.append(f"{plan['game']['name']}: {exc}")
    return moved, skipped


def _referenced(channel_id):
    with db.connect() as conn:
        for table, fields in (
            ("lfg_events", ("channel_id", "dashboard_channel_id", "private_channel_id", "voice_channel_id")),
            ("lfg_event_messages", ("channel_id",)),
            ("temp_voice_channels", ("channel_id",)),
            ("streamer_channels", ("channel_id",)),
        ):
            where = " OR ".join(f"{field}=?" for field in fields)
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE {where} LIMIT 1",
                [channel_id] * len(fields),
            ).fetchone():
                return True
        if conn.execute("SELECT 1 FROM settings WHERE value=? LIMIT 1", (str(channel_id),)).fetchone():
            return True
        if conn.execute(
            "SELECT 1 FROM games WHERE channel_id=? OR category_id=? OR chat_channel_id=? OR "
            "lfg_channel_id=? OR clips_channel_id=? OR memes_channel_id=? OR create_voice_channel_id=? LIMIT 1",
            (channel_id,) * 7,
        ).fetchone():
            return True
    return False


def cleanup_preview(guild, actor):
    if not authorized(guild, actor):
        raise ValueError("Administrator access is required.")
    candidates, review = [], []
    seen_categories = set()
    for game in db.get_all_games():
        hints = channels.legacy_hints(game)
        category_id = hints.get("category_id")
        if not category_id or category_id in seen_categories:
            continue
        category = guild.get_channel(int(category_id))
        if not isinstance(category, discord.CategoryChannel):
            continue
        seen_categories.add(category.id)

        known_ids = {
            int(value)
            for key, value in hints.items()
            if key.endswith("_channel_id") and value and key != "chat_channel_id"
        }
        unknown = [child for child in category.channels if child.id not in known_ids]
        occupied = [child for child in category.voice_channels if child.members]
        referenced = [child for child in category.channels if _referenced(child.id)]

        if unknown or occupied or referenced or _referenced(category.id):
            reasons = []
            if unknown:
                reasons.append("unknown child channel")
            if occupied:
                reasons.append("occupied voice")
            if referenced:
                reasons.append("stored dependency")
            if _referenced(category.id):
                reasons.append("category dependency")
            review.append(f"{game['name']}: {', '.join(reasons)}")
            continue

        candidates.append({
            "game_id": game["id"],
            "game_name": game["name"],
            "category_id": category.id,
            "category_wire": wire(category),
            "children": [(child.id, wire(child)) for child in category.channels],
        })

    return {
        "guild_id": guild.id,
        "actor_id": actor.id,
        "created": time.time(),
        "candidates": candidates,
        "review": review,
    }


def cleanup_text(draft):
    lines = [
        "# 🧹 Legacy Game Area Cleanup",
        "",
        f"Safe candidates: **{len(draft['candidates'])}**",
        f"Blocked/review: **{len(draft['review'])}**",
        "",
    ]
    for row in draft["candidates"][:15]:
        lines.append(
            f"• {row['game_name']} — delete old category + {len(row['children'])} known obsolete child channel(s)"
        )
    for warning in draft["review"][:5]:
        lines.append(f"⚠️ {warning[:170]}")
    lines.extend([
        "",
        "This deletes only confidently mapped legacy LFG/create-voice/etc. resources. "
        "The migrated game chat, game role, catalog entry and member selections remain. "
        "Deletion is irreversible and history in deleted legacy channels is lost.",
    ])
    return "\n".join(lines)[:1950]


async def cleanup(guild, actor, draft):
    if not authorized(guild, actor) or actor.id != draft["actor_id"] or guild.id != draft["guild_id"]:
        raise ValueError("This cleanup preview belongs to another administrator/server.")
    if time.time() - draft["created"] > 240:
        raise ValueError("Cleanup preview expired.")

    fresh = cleanup_preview(guild, actor)
    expected = {
        (row["game_id"], row["category_id"], row["category_wire"], tuple(row["children"]))
        for row in draft["candidates"]
    }
    actual = {
        (row["game_id"], row["category_id"], row["category_wire"], tuple(row["children"]))
        for row in fresh["candidates"]
    }
    if expected != actual:
        raise ValueError("Legacy game areas changed. Open a fresh cleanup preview.")

    removed = []
    from services.structure_adoption_service import expect_delete
    for row in draft["candidates"]:
        category = await guild.fetch_channel(row["category_id"])
        if not isinstance(category, discord.CategoryChannel) or wire(category) != row["category_wire"]:
            raise ValueError("A legacy category changed during cleanup.")

        snapshot = await guild.fetch_channels()
        for child_id, child_wire in row["children"]:
            child = next((c for c in snapshot if c.id == child_id), None)
            if child is None or wire(child) != child_wire or _referenced(child_id):
                raise ValueError("A legacy child changed or gained a dependency. Cleanup stopped.")
            if isinstance(child, discord.VoiceChannel) and child.members:
                raise ValueError("A legacy voice channel became occupied. Cleanup stopped.")
            expect_delete(guild, child.id)
            await child.delete(reason="Confirmed GamerHQ legacy game-area cleanup")

        expect_delete(guild, category.id)
        await category.delete(reason="Confirmed GamerHQ legacy game-area cleanup")
        removed.append(row["game_name"])

    return removed
