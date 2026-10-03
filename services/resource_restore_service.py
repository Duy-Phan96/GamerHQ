"""Explicit owner restore of intentionally removed managed channels."""
from __future__ import annotations

import time
from types import SimpleNamespace

import discord

from database import db
from services.server_service import ServerMessageError
from services.server_setup_service import SERVER_BLUEPRINT, normalize_name


def removed_names(guild):
    prefix = f"managed_channel_removed:{guild.id}:"
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT key FROM settings WHERE key LIKE ? AND value='1' ORDER BY key",
            (prefix + "%",),
        ).fetchall()
    return [row["key"][len(prefix):] for row in rows]


def definition(guild, name):
    aliases = {"purchases": "ig-purchases", "buyer-ranking": "ig-buyer-ranking"}
    for category in SERVER_BLUEPRINT:
        parent_name = normalize_name(category.name)
        parent_name = "partners-benefits" if parent_name == "marketplace" else parent_name
        for channel in category.channels:
            child = aliases.get(normalize_name(channel.name), normalize_name(channel.name))
            if child == name:
                return {
                    "key": f"managed_channel:{guild.id}:{child}",
                    "kind": channel.kind,
                    "name": child,
                    "label": channel.name,
                    "aliases": {child, normalize_name(channel.name)},
                    "private": category.private,
                    "parent": f"managed_category:{guild.id}:{parent_name}",
                }
    return None


def preview(guild, owner, name):
    if owner.id != guild.owner_id:
        raise ServerMessageError("Only the server owner can restore removed server resources.")
    if name not in removed_names(guild):
        raise ServerMessageError("This resource is no longer marked as intentionally removed.")
    row = definition(guild, name)
    if row is None:
        raise ServerMessageError("This removed resource has no safe default restore definition.")
    parent_raw = db.get_setting(row["parent"])
    parent = guild.get_channel(int(parent_raw)) if parent_raw and str(parent_raw).isdigit() else None
    if not isinstance(parent, discord.CategoryChannel):
        raise ServerMessageError("Restore/link the parent category first.")
    matches = [
        c for c in guild.channels
        if getattr(c, "name", None) and normalize_name(c.name) in row["aliases"]
    ]
    if matches:
        raise ServerMessageError("A same-name Discord resource already exists. Review/adopt it instead of creating a duplicate.")
    return {
        "guild_id": guild.id,
        "actor_id": owner.id,
        "name": name,
        "row": row,
        "parent_id": parent.id,
        "created": time.time(),
    }


async def restore(guild, owner, draft):
    if owner.id != guild.owner_id or draft["actor_id"] != owner.id or draft["guild_id"] != guild.id:
        raise ServerMessageError("Only the server owner who opened this preview can restore it.")
    if time.time() - draft["created"] > 180:
        raise ServerMessageError("Restore preview expired.")
    fresh = preview(guild, owner, draft["name"])
    if fresh["parent_id"] != draft["parent_id"] or fresh["row"] != draft["row"]:
        raise ServerMessageError("Server structure changed. Open a fresh restore preview.")

    row = draft["row"]
    parent = guild.get_channel(draft["parent_id"])
    from services import server_operations as operations
    blank = SimpleNamespace(
        guild=guild, name=row["label"], id=0, category=parent,
        overwrites={}, overwrites_for=lambda _: discord.PermissionOverwrite(),
    )
    rights = operations.rights(guild, row, blank)
    create = parent.create_voice_channel if row["kind"] == "voice" else parent.create_text_channel
    resource = await create(row["label"], overwrites=rights, reason="Confirmed GamerHQ removed-resource restore")
    operations.persist(guild, row, resource)
    db.set_setting(f"managed_channel_removed:{guild.id}:{row['name']}", "0")

    from services import structure_adoption_service as structure
    structure.save_runtime_state(guild, "channel", row["name"], structure.snapshot_channel(resource))
    change = structure.record_change(
        guild, "channel", row["name"], resource.id, owner.id, "channel_restore",
        {"removed": True}, structure.snapshot_channel(resource), reversible=False,
    )
    await structure._announce(
        guild, change, "Managed Channel Restored",
        f"{resource.mention} was explicitly restored by the server owner.",
    )

    # Rebuild feature-owned board content only when its existing refresh path knows it.
    try:
        from services.community_structure_service import refresh_boards
        await refresh_boards(guild)
    except Exception:
        pass
    return resource
