"""Owner-confirmed recovery for stale or ambiguous game text-channel mappings.

Read-only preview uses stored IDs only. It never adopts a channel by name. Apply
revalidates the exact preview, then either relinks a verified existing text chat
or clears confirmed-dead mappings before delegating to the normal visibility
lifecycle. Existing channels are never deleted by this service.
"""
from __future__ import annotations

import copy
import json
import time

import discord
from database import db
from services.authorization_service import authorized
from services import game_channel_service as channels
from services import game_visibility_service as visibility

MAX_AGE = 240


def _ids(game):
    hints = channels.legacy_hints(game)
    values = []
    for source, value in (("channel_id", game.get("channel_id")),
                          ("chat_channel_id", hints.get("chat_channel_id"))):
        if value in (None, ""):
            continue
        try:
            identifier = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"The saved {source} is invalid. Review the game mapping manually.") from None
        if identifier > 0 and identifier not in [row["id"] for row in values]:
            values.append({"source": source, "id": identifier})
    return hints, values


def _other_references(game_id, channel_id):
    for game in db.get_all_games():
        if game["id"] != game_id and game.get("channel_id") == channel_id:
            return True
    with db.connect() as conn:
        for row in conn.execute("SELECT game_id,resources_json FROM game_legacy_hints WHERE game_id!=?", (game_id,)):
            try:
                data = json.loads(row["resources_json"])
            except (TypeError, ValueError):
                continue
            if channel_id in [v for k, v in data.items() if k.endswith("_channel_id")]:
                return True
    return False


def _created_reservation(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("state") != "CREATED":
        return None
    value = data.get("channel_id", data.get("id"))
    return value if type(value) is int and value > 0 else None


async def preview(guild, actor, game_id):
    """Read-only recovery preview from one fresh channel/role inventory."""
    if not authorized(guild, actor):
        raise ValueError("Administrator access is required.")
    game = db.get_game_by_id(game_id)
    if not game or not game["active"]:
        raise ValueError("This game is no longer active.")
    if not guild.me or not guild.me.guild_permissions.manage_channels or not guild.me.guild_permissions.manage_roles:
        raise ValueError("The bot needs Manage Channels and Manage Roles for game-channel recovery.")

    fresh = await visibility._snapshot(guild)
    if not authorized(guild, actor):
        raise ValueError("Administrator access changed. Reopen Manage Visible Games.")

    hints, saved = _ids(game)
    candidates, missing, wrong_type = [], [], []
    role, _ = visibility._role(fresh, game, True)

    for row in saved:
        resource = fresh.get_channel(row["id"])
        if resource is None:
            missing.append(row)
            continue
        if not isinstance(resource, discord.TextChannel):
            wrong_type.append(row)
            continue
        if _other_references(game_id, resource.id):
            raise ValueError("A saved chat is also referenced by another game. Review ownership before changing it.")
        channels.dependencies(game_id, resource.id)
        visibility._validate_access(fresh, role, resource)
        candidates.append({"id": resource.id, "name": resource.name, "source": row["source"],
                           "category_id": resource.category_id})

    if wrong_type:
        raise ValueError("A saved game-chat ID now points to a non-text resource. It will not be cleared automatically.")

    reservation = db.get_setting(f"game_channel_creation:{guild.id}:{game_id}")
    reserved_id = _created_reservation(reservation)
    if reservation and reserved_id is None:
        raise ValueError("An earlier game-channel creation has an unverified result. Review it before retrying.")
    if reserved_id and fresh.get_channel(reserved_id) is not None and reserved_id not in [c["id"] for c in candidates]:
        raise ValueError("A previous creation points to another existing resource. Review it before recovery.")

    # A completed reservation whose returned channel is now absent is stale
    # evidence, but clearing it still requires this explicit confirmation flow.
    stale_ids = sorted({row["id"] for row in missing} | ({reserved_id} if reserved_id and fresh.get_channel(reserved_id) is None else set()))
    return {
        "guild_id": guild.id,
        "actor_id": actor.id,
        "game_id": game_id,
        "game": copy.deepcopy(game),
        "name": game["name"],
        "created": time.time(),
        "used": False,
        "candidates": candidates,
        "stale_ids": stale_ids,
        "saved_sources": saved,
        "allow_new": bool(stale_ids or candidates),
        "reason": (
            "Existing saved chat found. You can move/reuse it under 🎮 Games, or create a new chat instead."
            if candidates else
            "The previously saved game chat no longer exists. You can clear the outdated mapping and create a new chat."
            if stale_ids else
            "No stored game-chat evidence was found. Use the normal Set up action."
        ),
    }


def _same(a, b):
    keys = ("guild_id", "actor_id", "game_id", "game", "candidates", "stale_ids", "saved_sources", "allow_new")
    return all(a.get(key) == b.get(key) for key in keys)


def _clear_stale_mapping(guild, game_id, stale_ids):
    stale = set(stale_ids)
    game = db.get_game_by_id(game_id)
    hints = channels.legacy_hints(game)
    with db.connect() as conn:
        if game.get("channel_id") in stale:
            conn.execute("UPDATE games SET channel_id=NULL WHERE id=?", (game_id,))
        if game.get("chat_channel_id") in stale:
            conn.execute("UPDATE games SET chat_channel_id=NULL WHERE id=?", (game_id,))
        row = conn.execute("SELECT resources_json FROM game_legacy_hints WHERE game_id=?", (game_id,)).fetchone()
        if row:
            try:
                data = json.loads(row["resources_json"])
            except (TypeError, ValueError):
                raise ValueError("Saved legacy game evidence is invalid. Review it manually.") from None
            if data.get("chat_channel_id") in stale:
                data.pop("chat_channel_id", None)
                conn.execute("UPDATE game_legacy_hints SET resources_json=? WHERE game_id=?",
                             (json.dumps(data, sort_keys=True), game_id))
        reservation_key = f"game_channel_creation:{guild.id}:{game_id}"
        reservation = conn.execute("SELECT value FROM settings WHERE key=?", (reservation_key,)).fetchone()
        reserved_id = _created_reservation(reservation["value"] if reservation else None)
        if reserved_id in stale:
            conn.execute("DELETE FROM settings WHERE key=?", (reservation_key,))
        conn.execute("DELETE FROM settings WHERE key=?", (f"game_channel_removed:{guild.id}:{game_id}",))


async def apply(guild, actor, draft, choice):
    """Apply one explicitly chosen recovery and delegate final setup to Show."""
    if (draft.get("used") or time.time() - draft.get("created", 0) > MAX_AGE
            or guild.id != draft.get("guild_id") or actor.id != draft.get("actor_id")
            or not authorized(guild, actor)):
        raise ValueError("This recovery review expired, was used, or access changed. Open a fresh review.")
    draft["used"] = True

    fresh = await preview(guild, actor, draft["game_id"])
    if not _same(draft, fresh):
        raise ValueError("The game or its saved chat evidence changed. Reopen the recovery review.")

    if choice == "new":
        if not fresh["allow_new"]:
            raise ValueError("A new channel is not available from this recovery state.")
        stale = set(fresh["stale_ids"])
        # Choosing new explicitly abandons valid legacy candidates without
        # deleting them. Their IDs are removed only from this game's chat hints.
        stale.update(c["id"] for c in fresh["candidates"])
        _clear_stale_mapping(guild, draft["game_id"], sorted(stale))
    elif isinstance(choice, int):
        candidate = next((c for c in fresh["candidates"] if c["id"] == choice), None)
        if candidate is None:
            raise ValueError("That existing chat is no longer a verified recovery candidate.")
        with db.connect() as conn:
            conn.execute("UPDATE games SET channel_id=? WHERE id=?", (choice, draft["game_id"]))
            conn.execute("DELETE FROM settings WHERE key=?", (f"game_channel_removed:{guild.id}:{draft['game_id']}",))
    else:
        raise ValueError("Choose an existing chat or create a new one.")

    plan = await visibility.preview(guild, actor, draft["game_id"], True)
    channel = await visibility.apply(guild, actor, plan, override=plan["override"])
    return channel
