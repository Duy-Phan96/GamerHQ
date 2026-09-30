"""Read-only Gateway observations for the existing Owner Change Log.

This is an adapter to structure_change_log and owner_change_feed, not a second
history or resource manager. Observed state never becomes managed desired state.
Legacy mapped layout/deletion handling remains in ServerChanges.
"""
from __future__ import annotations

import asyncio
import json
import time

import discord
from database import db
from services import owner_change_feed as feed
from services import structure_adoption_service as structure

_locks = {}
_legacy_undos = {}
PREFIX = "owner_observation:"
EDITABLE = {
    "channel": {"name", "topic", "nsfw", "slowmode_delay", "bitrate", "user_limit", "rtc_region"},
    "category": {"name"},
    "role": {"name", "colour", "hoist", "mentionable"},
    "guild": {"name", "description", "afk_timeout"},
}
ATTRS = {
    "channel": ("name", "type", "topic", "nsfw", "position", "category_id", "slowmode_delay",
                "bitrate", "user_limit", "rtc_region", "default_auto_archive_duration",
                "default_thread_slowmode_delay", "default_sort_order", "default_layout", "flags"),
    "category": ("name", "position", "nsfw"),
    "role": ("name", "colour", "permissions", "hoist", "mentionable", "position", "managed", "unicode_emoji"),
    "guild": ("name", "description", "afk_timeout", "verification_level", "explicit_content_filter",
              "default_notifications", "preferred_locale", "system_channel_flags", "premium_progress_bar_enabled"),
}


def key(guild_id, kind, resource_id):
    return f"{PREFIX}{guild_id}:{kind}:{resource_id}"


def _value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return getattr(value, "value", str(value))


def snapshot(resource, kind):
    state = {"id": int(resource.id)}
    for field in ATTRS[kind]:
        if hasattr(resource, field):
            state[field] = _value(getattr(resource, field))
    if kind in {"channel", "category"}:
        state["overwrites"] = {}
        for target, overwrite in getattr(resource, "overwrites", {}).items():
            allow, deny = overwrite.pair()
            state["overwrites"][str(target.id)] = [allow.value, deny.value]
        if hasattr(resource, "available_tags"):
            state["available_tags"] = [
                [tag.id, tag.name, tag.moderated, str(tag.emoji) if tag.emoji else None]
                for tag in resource.available_tags
            ]
    if kind == "guild":
        for field in ("afk_channel", "system_channel", "rules_channel", "public_updates_channel"):
            state[field + "_id"] = getattr(getattr(resource, field, None), "id", None)
        state["features"] = sorted(getattr(resource, "features", []))
    for field in (("icon", "banner", "splash") if kind == "guild" else ("icon",) if kind == "role" else ()):
        asset = getattr(resource, field, None)
        state[field] = str(asset) if asset is not None else None
    return state


def kind_of(resource):
    if isinstance(resource, discord.CategoryChannel):
        return "category"
    if isinstance(resource, discord.Role):
        return "role"
    if isinstance(resource, discord.Guild):
        return "guild"
    return "channel"


def legacy_fields(guild, resource, kind):
    """Only fields already recorded by the legacy managed-layout observers."""
    if kind == "channel" and structure.channel_mapping(guild, resource.id):
        return {"name", "category_id", "position"}
    if kind == "category" and structure.category_mapping(guild, resource.id):
        return {"name", "position"}
    return set()


def history_mark(guild):
    with db.connect() as conn:
        return conn.execute("SELECT COALESCE(MAX(id),0) FROM structure_change_log WHERE guild_id=?",
                            (guild.id,)).fetchone()[0]


def covered_fields(guild, resource, kind, mark, *, deleted=False):
    with db.connect() as conn:
        rows = conn.execute("SELECT action,after_json FROM structure_change_log "
                            "WHERE guild_id=? AND resource_id=? AND id>?",
                            (guild.id, resource.id, mark)).fetchall()
    if deleted:
        return any(row["action"] in {"channel_delete", "category_delete"} for row in rows)
    current = snapshot(resource, kind)
    fields = {"name", "position", "category_id"} if kind == "channel" else {"name", "position"}
    for row in rows:
        if row["action"] not in {"channel_update", "category_update", "security_review"}:
            continue
        after = json.loads(row["after_json"])
        if all(after.get(field) == current.get(field) for field in fields):
            return fields
    return set()


def _store(conn, setting_key, value):
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                 (setting_key, json.dumps(value, sort_keys=True)))


def _record(conn, guild, kind, resource_id, before, after, *, ignore=(), offline=False, record=True):
    """Snapshot and history insertion share one transaction, including deduplication."""
    setting_key = key(guild.id, kind, resource_id)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (setting_key,)).fetchone()
    previous = json.loads(row["value"]) if row else None
    if previous == after:
        return False
    fields = sorted(field for field in set(before) | set(after)
                    if field != "id" and field not in ignore and before.get(field) != after.get(field))
    _store(conn, setting_key, after)
    if not record or not fields:
        return False
    suffix = "offline" if offline else "delete" if after.get("deleted") else "create" if before.get("deleted") else "update"
    saved_after = dict(after, _fields=fields)
    action = f"observed_{kind}_{suffix}"
    change = {"action": action, "resource_type": kind, "before": before, "after": saved_after,
              "status": "APPLIED", "reversible": True, "resource_id": resource_id, "guild_id": guild.id}
    reversible = can_undo(change)
    conn.execute(
        "INSERT INTO structure_change_log "
        "(guild_id,resource_type,logical_key,resource_id,actor_id,action,before_json,after_json,reversible,status,created_at) "
        "VALUES(?,?,?,?,NULL,?,?,?,?,?,?)",
        (guild.id, kind, f"discord:{kind}:{resource_id}", resource_id, action,
         json.dumps(before, sort_keys=True), json.dumps(saved_after, sort_keys=True),
         int(reversible), "APPLIED", int(time.time())),
    )
    return True


def _legacy_undo_fields(guild, kind, resource_id, old, new):
    """Consume only an explicitly started legacy Undo's exact inverse fields."""
    now = time.monotonic()
    for token, pending in list(_legacy_undos.items()):
        if pending["expires"] <= now:
            _legacy_undos.pop(token, None)
            continue
        if token[:2] != (guild.id, resource_id) or pending["kind"] != kind:
            continue
        fields = pending["fields"]
        if all(old.get(field) == pending["after"].get(field)
               and new.get(field) == pending["before"].get(field) for field in fields):
            _legacy_undos.pop(token, None)
            return set(fields)
    return set()


async def undo_legacy(guild, owner, change_id):
    """Preserve legacy Undo while avoiding a new inverse notice from its event."""
    if owner.id != guild.owner_id:
        raise ValueError("Only the current server owner can undo this change.")
    change = structure.get_change(guild, change_id)
    token = None
    if change and change["status"] == "APPLIED" and change["action"] in {"channel_update", "category_update"}:
        fields = [field for field in ("name", "position", "category_id")
                  if change["before"].get(field) != change["after"].get(field)]
        if fields:
            token = (guild.id, change["resource_id"], change_id)
            _legacy_undos[token] = {"kind": change["resource_type"], "fields": fields,
                                   "before": change["before"], "after": change["after"],
                                   "expires": time.monotonic() + 90}
    try:
        return await structure.undo_change(guild, owner, change_id)
    except BaseException:
        if token is not None:
            _legacy_undos.pop(token, None)
        raise


async def observe(guild, kind, before, after, *, ignore=(), record=True):
    """Record promptly, without waiting for audit attribution or changing Discord."""
    resource = after if after is not None else before
    old = snapshot(before, kind) if before is not None else {"id": resource.id, "deleted": True}
    new = snapshot(after, kind) if after is not None else {"id": resource.id, "deleted": True}
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        # Enable before recording, even when the destination is temporarily unavailable.
        # This prevents a later first successful delivery from skipping the backlog.
        feed.enable(guild)
        ignore = set(ignore) | _legacy_undo_fields(guild, kind, resource.id, old, new)
        with db.connect() as conn:
            changed = _record(conn, guild, kind, resource.id, old, new, ignore=ignore, record=record)
    if changed:
        await feed.flush(guild)
    return changed


async def reconcile(guild):
    """Ready/resume net-difference check; a first baseline is not a set of changes.

    Missing cached resources are reported as unavailable offline, not as proven
    deletions. No intermediate events, actors, or reversible history are invented.
    """
    if getattr(guild, "unavailable", False) is True:
        return
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        feed.enable(guild)
        inventory = [(kind_of(item), item) for item in guild.channels]
        inventory += [("role", item) for item in guild.roles] + [("guild", guild)]
        complete_key = f"owner_observation_baseline:{guild.id}"
        with db.connect() as conn:
            seeded = conn.execute("SELECT value FROM settings WHERE key=?", (complete_key,)).fetchone()
            rows = conn.execute("SELECT key,value FROM settings WHERE key LIKE ?", (f"{PREFIX}{guild.id}:%",)).fetchall()
            saved = {row["key"]: json.loads(row["value"]) for row in rows}
            seen = set()
            for kind, resource in inventory:
                setting_key = key(guild.id, kind, resource.id)
                seen.add(setting_key)
                current = snapshot(resource, kind)
                previous = saved.get(setting_key)
                if previous is None and not seeded:
                    _store(conn, setting_key, current)
                else:
                    _record(conn, guild, kind, resource.id,
                            previous or {"id": resource.id, "deleted": True}, current,
                            ignore=legacy_fields(guild, resource, kind), offline=True)
            if seeded:
                for setting_key in set(saved) - seen:
                    kind, resource_id = setting_key.rsplit(":", 2)[1:]
                    previous = saved[setting_key]
                    _record(conn, guild, kind, int(resource_id), previous,
                            {"id": int(resource_id), "deleted": True}, offline=True)
            _store(conn, complete_key, True)
            # An interrupted remote write has an unknown outcome: never retry Undo blindly.
            conn.execute("UPDATE structure_change_log SET status='REVIEW_REQUIRED' "
                         "WHERE guild_id=? AND action LIKE 'observed_%' AND status='UNDOING'", (guild.id,))
    await feed.flush(guild)


def is_observation(change):
    return bool(change and change.get("action", "").startswith("observed_"))


def can_undo(change):
    if not is_observation(change) or change.get("status") != "APPLIED" or not change.get("reversible"):
        return False
    if not change["action"].endswith("_update"):
        return False
    fields = set(change["after"].get("_fields", []))
    kind = change["resource_type"]
    if not fields or not fields <= EDITABLE.get(kind, set()):
        return False
    if kind == "role" and (change["after"].get("managed") or change["resource_id"] == change.get("guild_id")):
        return False
    return all(field in change["before"] and field in change["after"] for field in fields)


def detail_text(change):
    def safe(value):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        return discord.utils.escape_mentions(discord.utils.escape_markdown(text))[:75]
    lines = [f"# Change #{change['id']} · Server configuration", f"Resource: {safe(change['logical_key'])}",
             f"Status: **{safe(change['status'])}**", "Actor: Unknown (no reliably correlated audit evidence).", ""]
    fields = change["after"].get("_fields", [])
    for field in fields[:7]:
        lines.append(f"**{safe(field)}:** {safe(change['before'].get(field))} → {safe(change['after'].get(field))}")
    if len(fields) > 7:
        lines.append(f"… {len(fields) - 7} further changed fields are saved in the private history.")
    if change["action"].endswith("_offline"):
        lines.append("Offline net difference only: an unavailable cached resource is not proof of deletion.")
    if can_undo(change):
        lines.append("\nConfirm Undo restores only the listed changed fields, after a fresh conflict check.")
    else:
        lines.append("\nAutomatic Undo is not available for this change. Permissions, ordering, creation/deletion "
                     "and offline differences require manual review. Deleted history cannot be recovered.")
    return "\n".join(lines)[:1950]


async def _fetch(guild, kind, resource_id, client):
    if kind in {"channel", "category"}:
        return await guild.fetch_channel(resource_id)
    if kind == "role":
        roles = await guild.fetch_roles()
        return next((role for role in roles if role.id == resource_id), None)
    return await client.fetch_guild(guild.id)


def _status(guild, change_id, status):
    with db.connect() as conn:
        conn.execute("UPDATE structure_change_log SET status=? WHERE guild_id=? AND id=? AND status='UNDOING'",
                     (status, guild.id, change_id))


async def undo(guild, owner, change_id, client):
    """Owner-confirmed, field-limited Undo with durable execution reservation."""
    if owner.id != guild.owner_id:
        raise ValueError("Only the current server owner can undo this change.")
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        change = structure.get_change(guild, change_id)
        if not can_undo(change):
            raise ValueError("This change is not available for automatic Undo.")
        authority = await client.fetch_guild(guild.id)
        if authority.owner_id != owner.id or guild.owner_id != owner.id:
            raise ValueError("Server ownership changed. Reopen the review.")
        kind, resource_id = change["resource_type"], int(change["resource_id"])
        resource = await _fetch(guild, kind, resource_id, client)
        if resource is None:
            raise ValueError("The resource no longer exists. Undo cannot recreate it.")
        current = snapshot(resource, kind)
        fields = change["after"]["_fields"]
        if any(current.get(field) != change["after"].get(field) for field in fields):
            raise ValueError("These fields changed again. Review the latest change before undoing this one.")
        if kind == "role":
            if resource.managed or resource.id == guild.id:
                raise ValueError("This role cannot be edited safely.")
            member = await guild.fetch_member(guild.me.id)
            if not member.guild_permissions.manage_roles or resource >= member.top_role:
                raise ValueError("The bot needs Manage Roles and a higher role to undo this change.")
        authority = await client.fetch_guild(guild.id)
        if authority.owner_id != owner.id or owner.id != guild.owner_id:
            raise ValueError("Server ownership changed. Reopen the review.")
        kwargs = {field: change["before"][field] for field in fields}
        if kind == "role" and "colour" in kwargs:
            kwargs["colour"] = discord.Colour(kwargs["colour"])
        with db.connect() as conn:
            claimed = conn.execute("UPDATE structure_change_log SET status='UNDOING' "
                                   "WHERE guild_id=? AND id=? AND status='APPLIED'",
                                   (guild.id, change_id)).rowcount
        if not claimed:
            raise ValueError("This change was already handled or is being undone.")
        try:
            if kind in {"channel", "category"} and "name" in kwargs:
                # The existing mapped-layout observer must not announce our own Undo.
                from services.channel_change_service import expect
                expect(guild, resource_id, name=kwargs["name"])
            await resource.edit(**kwargs, reason=f"GamerHQ owner undo change #{change_id}")
            verified = await _fetch(guild, kind, resource_id, client)
            actual = snapshot(verified, kind) if verified is not None else {}
            if any(actual.get(field) != change["before"].get(field) for field in fields):
                raise ValueError("Discord did not confirm the complete Undo. Review the current resource; do not retry blindly.")
        except discord.HTTPException as exc:
            # Only an explicit rejection before any successful edit is safe to retry.
            _status(guild, change_id, "REVIEW_REQUIRED")
            raise ValueError("Discord could not verify this Undo. Review the resource and the bot's permissions.") from exc
        except BaseException:
            _status(guild, change_id, "REVIEW_REQUIRED")
            raise
        # Only a confirmed Undo may update a mapped display name. Observation
        # alone never adopts the external snapshot as desired application state.
        runtime_update = None
        if kind in {"channel", "category"} and "name" in fields:
            logical = (structure.channel_mapping(guild, resource_id) if kind == "channel"
                       else structure.category_mapping(guild, resource_id))
            if logical:
                intended = structure.runtime_state(guild, kind, logical)
                if not intended or int(intended.get("id") or 0) == resource_id:
                    intended = dict(intended or (structure.snapshot_channel(verified) if kind == "channel"
                                                else structure.snapshot_category(verified)))
                    intended["name"] = actual["name"]
                    runtime_update = (structure._runtime_key(guild.id, kind, logical), intended)
        with db.connect() as conn:
            if runtime_update is not None:
                _store(conn, *runtime_update)
            _store(conn, key(guild.id, kind, resource_id), actual)
            conn.execute("UPDATE structure_change_log SET status='UNDONE',undone_at=?,undone_by=? "
                         "WHERE guild_id=? AND id=? AND status='UNDOING'",
                         (int(time.time()), owner.id, guild.id, change_id))
    await feed.flush(guild)
    return structure.get_change(guild, change_id)
