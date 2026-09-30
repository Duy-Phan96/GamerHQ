"""Runtime Discord structure adoption and owner-visible reversible change history.

Discord IDs remain identity. Safe display/layout changes to already mapped resources
become runtime configuration. Domain/application data stays authoritative in SQLite.
Security-sensitive changes are recorded for review instead of silently adopted.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import discord

from database import db
from services.onboarding_service import alias

log = logging.getLogger(__name__)

RUNTIME_PREFIX = "runtime_structure"
PROTECTED_CHANNELS = {
    "lobby-admin", "owner-changelog", "server-log", "games-log", "bot-log",
    "staff-chat", "staff-suggestions", "ticket-logs", "mod-log", "mod-commands",
    "ig-purchases", "ig-buyer-ranking",
}
PROTECTED_CATEGORIES = {"staff", "affiliate-stats", "support-tickets"}
_expected_deletes = {}
_locks = {}


def _runtime_key(guild_id: int, resource_type: str, logical_key: str) -> str:
    return f"{RUNTIME_PREFIX}:{guild_id}:{resource_type}:{logical_key}"


def _loads(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def runtime_state(guild, resource_type: str, logical_key: str) -> dict:
    return _loads(db.get_setting(_runtime_key(guild.id, resource_type, logical_key)))


def save_runtime_state(guild, resource_type: str, logical_key: str, state: dict) -> None:
    db.set_setting(_runtime_key(guild.id, resource_type, logical_key), json.dumps(state, sort_keys=True))


def channel_mapping(guild, channel_id: int):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT key FROM settings WHERE key LIKE ? AND value=?",
            (f"managed_channel:{guild.id}:%", str(channel_id)),
        ).fetchall()
        game_rows = conn.execute("SELECT id FROM games WHERE channel_id=?", (channel_id,)).fetchall()
    logical = [row["key"].split(":", 2)[2] for row in rows]
    logical += [f"game:{row['id']}" for row in game_rows]
    return logical[0] if len(logical) == 1 else None


def category_mapping(guild, category_id: int):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT key FROM settings WHERE key LIKE ? AND value=?",
            (f"managed_category:{guild.id}:%", str(category_id)),
        ).fetchall()
    logical = [row["key"].split(":", 2)[2] for row in rows]
    return logical[0] if len(logical) == 1 else None


def channel_state(guild, logical_key: str, *, channel_id=None) -> dict:
    state = runtime_state(guild, "channel", logical_key)
    if state and channel_id is not None and int(state.get("id") or 0) != int(channel_id):
        return {}
    return state


def category_state(guild, logical_key: str, *, category_id=None) -> dict:
    state = runtime_state(guild, "category", logical_key)
    if state and category_id is not None and int(state.get("id") or 0) != int(category_id):
        return {}
    return state


def snapshot_channel(channel) -> dict:
    return {
        "id": int(channel.id),
        "name": channel.name,
        "category_id": getattr(channel, "category_id", None),
        "position": int(getattr(channel, "position", 0)),
        "kind": "voice" if isinstance(channel, discord.VoiceChannel) else "text",
    }


def snapshot_category(category) -> dict:
    return {
        "id": int(category.id),
        "name": category.name,
        "position": int(getattr(category, "position", 0)),
    }


def _category_private(category) -> bool:
    if category is None:
        return False
    try:
        return category.overwrites_for(category.guild.default_role).view_channel is False
    except Exception:
        return False


def _protected_channel(guild, logical_key: str, channel) -> bool:
    if logical_key in PROTECTED_CHANNELS:
        return True
    parent = getattr(channel, "category", None)
    return bool(parent and (alias(parent.name) in PROTECTED_CATEGORIES or _category_private(parent)))


def _safe_parent(guild, logical_key: str, before_channel, before: dict, after: dict) -> bool:
    if before.get("category_id") == after.get("category_id"):
        return True
    target = guild.get_channel(after.get("category_id")) if after.get("category_id") else None
    if _protected_channel(guild, logical_key, before_channel):
        return bool(
            isinstance(target, discord.CategoryChannel)
            and _category_private(target)
            and alias(target.name) in PROTECTED_CATEGORIES
        )
    if target is None:
        return True
    return isinstance(target, discord.CategoryChannel) and not _category_private(target) and alias(target.name) not in PROTECTED_CATEGORIES


def expect_delete(guild, resource_id: int, ttl: float = 90.0):
    _expected_deletes[(guild.id, int(resource_id))] = time.monotonic() + ttl


def _consume_expected_delete(guild, resource_id: int) -> bool:
    now = time.monotonic()
    for key, expiry in list(_expected_deletes.items()):
        if expiry <= now:
            _expected_deletes.pop(key, None)
    return _expected_deletes.pop((guild.id, int(resource_id)), None) is not None


def _change_row(row):
    return {
        "id": row["id"], "guild_id": row["guild_id"], "resource_type": row["resource_type"],
        "logical_key": row["logical_key"], "resource_id": row["resource_id"], "actor_id": row["actor_id"],
        "action": row["action"], "before": _loads(row["before_json"]), "after": _loads(row["after_json"]),
        "reversible": bool(row["reversible"]), "status": row["status"], "created_at": row["created_at"],
        "undone_at": row["undone_at"], "undone_by": row["undone_by"],
    }


def record_change(guild, resource_type: str, logical_key: str, resource_id, actor_id, action: str,
                  before: dict, after: dict, *, reversible=False, status="APPLIED") -> dict:
    now = int(time.time())
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO structure_change_log "
            "(guild_id,resource_type,logical_key,resource_id,actor_id,action,before_json,after_json,reversible,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (guild.id, resource_type, logical_key, resource_id, actor_id, action,
             json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True),
             int(bool(reversible)), status, now),
        )
        row = conn.execute("SELECT * FROM structure_change_log WHERE id=?", (cur.lastrowid,)).fetchone()
    return _change_row(row)


def recent_changes(guild, limit=25):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM structure_change_log WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (guild.id, int(limit)),
        ).fetchall()
    return [_change_row(row) for row in rows]


def get_change(guild, change_id: int):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM structure_change_log WHERE guild_id=? AND id=?",
            (guild.id, int(change_id)),
        ).fetchone()
    return _change_row(row) if row else None


async def _announce(guild, change: dict, title: str, description: str):
    try:
        from services.server_log_service import emit
        await emit(guild, f"structure-change:{change['id']}:{change['status']}", title, description)
    except Exception:
        log.warning("Could not publish structure change to server log guild=%s", guild.id)
    try:
        from cogs.owner_changelog import refresh_board
        await refresh_board(guild)
    except Exception:
        pass


async def audit_actor(guild, target_id: int, action) -> int | None:
    try:
        me = guild.me
        if not me or not me.guild_permissions.view_audit_log:
            return None
        await asyncio.sleep(0.8)
        cutoff = time.time() - 20
        async for entry in guild.audit_logs(limit=6, action=action):
            target = getattr(entry, "target", None)
            created = entry.created_at.timestamp() if getattr(entry, "created_at", None) else 0
            if getattr(target, "id", None) == target_id and created >= cutoff:
                return getattr(getattr(entry, "user", None), "id", None)
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        return None
    return None


async def observe_channel_update(before, after, *, actor_id=None):
    logical = channel_mapping(after.guild, after.id)
    if not logical:
        return {"handled": False, "permissions_changed": False}
    from services import channel_change_service as tracked
    old_wire, new_wire = tracked.wire(before), tracked.wire(after)
    changed = {key: value for key, value in new_wire.items() if old_wire.get(key) != value}
    unmatched = tracked.consume(after.guild, after.id, changed)
    fields = [field for field in ("name", "category", "position") if field in unmatched]
    permissions_changed = "permissions" in unmatched
    if not fields and not permissions_changed:
        return {"handled": True, "permissions_changed": False}

    old_state = snapshot_channel(before)
    new_state = snapshot_channel(after)
    async with _locks.setdefault((after.guild.id, "channel", logical), asyncio.Lock()):
        if fields and ("category" not in fields or _safe_parent(after.guild, logical, before, old_state, new_state)):
            save_runtime_state(after.guild, "channel", logical, new_state)
            if not logical.startswith("game:"):
                db.set_setting(f"managed_channel_removed:{after.guild.id}:{logical}", "0")
            change = record_change(after.guild, "channel", logical, after.id, actor_id, "channel_update",
                                   old_state, new_state, reversible=True)
            await _announce(
                after.guild, change, "Discord Structure Adopted",
                f"<#{after.id}> changed in Discord; its mapped ID was preserved. Fields adopted: {', '.join(fields)}.",
            )
        elif fields:
            change = record_change(after.guild, "channel", logical, after.id, actor_id, "security_review",
                                   old_state, new_state, reversible=False, status="REVIEW_REQUIRED")
            await _announce(
                after.guild, change, "Security Review Required",
                f"Mapped channel <#{after.id}> moved into a location that cannot be adopted safely. "
                "The stored security contract was not changed.",
            )
    return {"handled": True, "permissions_changed": permissions_changed}


async def _retire_messages_in_channel(guild, channel_id: int):
    from services import managed_message_service as managed
    for state in list(managed.records(guild)):
        if int(state.get("channel_id") or 0) != int(channel_id):
            continue
        state["retired"] = True
        state["deleted_intentionally"] = True
        state["retired_at"] = int(time.time())
        managed.store(state)
        if str(db.get_setting(state["key"]) or "") == str(state.get("message_id")):
            db.set_setting(state["key"], "")


async def observe_channel_delete(channel, *, actor_id=None):
    guild = channel.guild
    if _consume_expected_delete(guild, channel.id):
        return False
    await asyncio.sleep(0.8)
    logical = channel_mapping(guild, channel.id)
    if not logical:
        return False
    before = snapshot_channel(channel)
    after = dict(before, deleted=True)
    if logical.startswith("game:"):
        game_id = int(logical.split(":", 1)[1])
        with db.connect() as conn:
            conn.execute("UPDATE games SET channel_id=NULL WHERE id=? AND channel_id=?", (game_id, channel.id))
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                         (f"game_channel_removed:{guild.id}:{game_id}", "1"))
    else:
        db.set_setting(f"managed_channel_removed:{guild.id}:{logical}", "1")
        db.set_setting(f"managed_channel:{guild.id}:{logical}", "")
        await _retire_messages_in_channel(guild, channel.id)
    save_runtime_state(guild, "channel", logical, after)
    change = record_change(guild, "channel", logical, channel.id, actor_id, "channel_delete",
                           before, after, reversible=False)
    await _announce(
        guild, change, "Managed Channel Removed",
        f"Mapped channel ID {channel.id} was deleted. It is intentionally absent and normal repair will not recreate it automatically.",
    )
    return True


async def observe_category_update(before, after, *, actor_id=None):
    logical = category_mapping(after.guild, after.id)
    if not logical:
        return False
    old_state, new_state = snapshot_category(before), snapshot_category(after)
    if old_state == new_state:
        return True
    save_runtime_state(after.guild, "category", logical, new_state)
    change = record_change(after.guild, "category", logical, after.id, actor_id, "category_update",
                           old_state, new_state, reversible=True)
    await _announce(
        after.guild, change, "Category Change Adopted",
        f"Category ID {after.id} changed in Discord. Its ID remains authoritative and the display state was stored.",
    )
    return True


async def observe_category_delete(category, *, actor_id=None):
    guild = category.guild
    if _consume_expected_delete(guild, category.id):
        return False
    await asyncio.sleep(0.8)
    logical = category_mapping(guild, category.id)
    if not logical:
        return False
    before = snapshot_category(category)
    after = dict(before, deleted=True)
    db.set_setting(f"managed_category:{guild.id}:{logical}", "")
    db.set_setting(f"managed_category_removed:{guild.id}:{logical}", "1")
    save_runtime_state(guild, "category", logical, after)
    change = record_change(guild, "category", logical, category.id, actor_id, "category_delete",
                           before, after, reversible=False)
    await _announce(
        guild, change, "Managed Category Removed",
        f"Category ID {category.id} was deleted. Normal repair will not recreate it; owner setup can restore a replacement.",
    )
    return True


async def observe_managed_message_delete(guild, channel_id: int, message_id: int, *, actor_id=None):
    from services import managed_message_service as managed
    await asyncio.sleep(0.8)
    matches = [state for state in managed.records(guild)
               if int(state.get("message_id") or 0) == int(message_id)
               and int(state.get("channel_id") or 0) == int(channel_id)]
    if len(matches) != 1:
        return False
    state = matches[0]
    if str(db.get_setting(state["key"]) or "") != str(message_id):
        return False
    before = {
        "setting_key": state["key"], "channel_id": channel_id, "message_id": message_id,
        "content": state["content"], "buttons": state["buttons"], "pinned": True,
    }
    state["retired"] = True
    state["deleted_intentionally"] = True
    state["retired_at"] = int(time.time())
    state["version"] = int(state.get("version", 0)) + 1
    managed.store(state)
    db.set_setting(state["key"], "")
    after = dict(before, deleted=True)
    change = record_change(guild, "message", state["key"], message_id, actor_id, "message_delete",
                           before, after, reversible=True)
    await _announce(
        guild, change, "Managed Message Removed",
        f"A registered GamerHQ message in <#{channel_id}> was deleted. It stays removed until explicitly restored.",
    )
    return True


def desired_channel_name(guild, logical_key: str, default: str) -> str:
    state = channel_state(guild, logical_key)
    return state.get("name", default) if not state.get("deleted") else default


def desired_channel_parent(guild, logical_key: str, default):
    state = channel_state(guild, logical_key)
    cid = state.get("category_id") if state and not state.get("deleted") else None
    target = guild.get_channel(int(cid)) if cid is not None else None
    return target if isinstance(target, discord.CategoryChannel) else default


async def undo_change(guild, owner, change_id: int):
    if owner.id != guild.owner_id:
        raise ValueError("Only the server owner can undo structure changes.")
    change = get_change(guild, change_id)
    if not change or change["status"] != "APPLIED" or not change["reversible"]:
        raise ValueError("This change is not available for Undo.")
    async with _locks.setdefault((guild.id, "undo", str(change_id)), asyncio.Lock()):
        change = get_change(guild, change_id)
        if not change or change["status"] != "APPLIED":
            raise ValueError("This change was already handled.")
        before, after = change["before"], change["after"]
        if change["action"] == "channel_update":
            channel = guild.get_channel(int(change["resource_id"]))
            if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                raise ValueError("The channel no longer exists; Undo cannot restore its history.")
            current = snapshot_channel(channel)
            for field in ("name", "category_id", "position"):
                if current.get(field) != after.get(field):
                    raise ValueError("The channel changed again. Review the latest change before undoing this one.")
            category = guild.get_channel(before.get("category_id")) if before.get("category_id") else None
            from services.channel_change_service import edit
            kwargs = {"name": before["name"], "position": before["position"]}
            if before.get("category_id") != current.get("category_id"):
                if before.get("category_id") and not isinstance(category, discord.CategoryChannel):
                    raise ValueError("The previous category no longer exists.")
                kwargs.update(category=category, sync_permissions=False)
            await edit(channel, **kwargs, reason=f"GamerHQ owner undo change #{change_id}")
            save_runtime_state(guild, "channel", change["logical_key"], before)
        elif change["action"] == "category_update":
            category = guild.get_channel(int(change["resource_id"]))
            if not isinstance(category, discord.CategoryChannel):
                raise ValueError("The category no longer exists.")
            current = snapshot_category(category)
            if current != after:
                raise ValueError("The category changed again. Review the latest change first.")
            from services.channel_change_service import expect
            expect(guild, category.id, name=before["name"], position=before["position"])
            await category.edit(name=before["name"], position=before["position"],
                                reason=f"GamerHQ owner undo change #{change_id}")
            save_runtime_state(guild, "category", change["logical_key"], before)
        elif change["action"] == "message_delete":
            from services import managed_message_service as managed
            state = managed.load(change["logical_key"])
            if not state or not state.get("retired"):
                raise ValueError("The managed message state changed; reopen the log.")
            channel = guild.get_channel(int(before["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                raise ValueError("The previous channel no longer exists.")
            managed.validate(guild, state["key"], state["content"], state["buttons"])
            message = await channel.send(
                state["content"], view=managed.render(state["buttons"]),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await message.pin(reason=f"GamerHQ owner undo change #{change_id}")
            db.set_setting(state["key"], message.id)
            state.update(
                message_id=message.id, retired=False, deleted_intentionally=False,
                pending=False, content_hash=managed.digest(state["content"]),
                version=int(state.get("version", 0)) + 1,
            )
            state.pop("retired_at", None)
            managed.store(state)
        else:
            raise ValueError("This change requires explicit restore rather than Undo.")

        now = int(time.time())
        with db.connect() as conn:
            conn.execute(
                "UPDATE structure_change_log SET status='UNDONE',undone_at=?,undone_by=? "
                "WHERE guild_id=? AND id=? AND status='APPLIED'",
                (now, owner.id, guild.id, change_id),
            )
        updated = get_change(guild, change_id)
        await _announce(guild, updated, "Owner Undo Applied", f"Change #{change_id} was reverted by the server owner.")
        return updated
