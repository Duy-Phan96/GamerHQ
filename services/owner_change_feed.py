"""Per-change owner-log notices backed by existing SQLite history and settings.

No Discord resources are discovered/created here. The history remains authoritative;
notices contain only generic metadata and point to owner-authorized private review.
"""
from __future__ import annotations

import asyncio
import json
import logging

import discord
from database import db
from services import structure_adoption_service as structure

log = logging.getLogger(__name__)
BATCH_SIZE = 10
GROUP_LOOKAHEAD = BATCH_SIZE * 2
GROUP_WINDOW_SECONDS = 4
_locks = {}


def cursor_key(guild_id):
    return f"owner_change_feed_cursor:{guild_id}"


def notice_key(guild_id, change_id):
    return f"owner_change_notice:{guild_id}:{change_id}"


def message_key(guild_id, channel_id, message_id):
    return f"owner_change_message:{guild_id}:{channel_id}:{message_id}"


def enable(guild, *, include_latest=False):
    """First activation starts now, not with a flood of historical notifications."""
    raw = db.get_setting(cursor_key(guild.id))
    if raw is not None:
        return int(raw)
    with db.connect() as conn:
        latest = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM structure_change_log WHERE guild_id=?", (guild.id,)
        ).fetchone()[0]
        start = max(0, int(latest) - int(include_latest))
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (cursor_key(guild.id), str(start)))
        return int(conn.execute("SELECT value FROM settings WHERE key=?", (cursor_key(guild.id),)).fetchone()[0])


def load_notice(guild_id, change_id):
    raw = db.get_setting(notice_key(guild_id, change_id))
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"state": "INVALID"}
    except (ValueError, TypeError):
        return {"state": "INVALID"}


def _store(guild_id, change_id, value):
    db.set_setting(notice_key(guild_id, change_id), json.dumps(value, sort_keys=True))


def _changed_fields(change):
    before, after = change.get("before") or {}, change.get("after") or {}
    explicit = set(after.get("_fields", ()))
    if explicit:
        return explicit
    ignored = {"_fields"}
    return {
        key for key in (set(before) | set(after))
        if key not in ignored and before.get(key) != after.get(key)
    }


def _is_category_delete(change):
    return bool(
        change
        and change.get("resource_type") == "category"
        and change.get("action") in {"category_delete", "observed_category_delete"}
        and change.get("status") == "APPLIED"
    )


def _is_related_channel_move(change, category_id):
    if not change or change.get("resource_type") != "channel":
        return False
    if change.get("action") not in {"channel_update", "observed_channel_update"}:
        return False
    if change.get("status") != "APPLIED":
        return False
    before, after = change.get("before") or {}, change.get("after") or {}
    if before.get("category_id") != category_id or after.get("category_id") == category_id:
        return False
    fields = _changed_fields(change)
    return bool("category_id" in fields and fields <= {"category_id", "position"})


def _group_plan(guild, changes):
    """Return ordered delivery units without rewriting authoritative history."""
    used = set()
    groups = {}
    for category in changes:
        if not _is_category_delete(category) or category["id"] in used:
            continue
        category_id = category.get("resource_id")
        nearby_deletes = [
            item for item in changes
            if item["id"] != category["id"]
            and _is_category_delete(item)
            and abs(int(item["created_at"]) - int(category["created_at"])) <= GROUP_WINDOW_SECONDS
        ]
        if nearby_deletes:
            continue
        related = [
            item for item in changes
            if _is_related_channel_move(item, category_id)
            and abs(int(item["created_at"]) - int(category["created_at"])) <= GROUP_WINDOW_SECONDS
        ]
        if len(related) < 2:
            continue
        ids = {category["id"], *(item["id"] for item in related)}
        low, high = min(ids), max(ids)
        between = [item for item in changes if low <= item["id"] <= high]
        if any(item["id"] not in ids for item in between):
            continue

        leader_notice = load_notice(guild.id, category["id"])
        if leader_notice and leader_notice.get("state") not in {"PENDING"}:
            continue
        allowed = True
        for child in related:
            state = load_notice(guild.id, child["id"])
            if state is None:
                continue
            if state.get("state") == "GROUPED" and state.get("parent_change_id") == category["id"]:
                continue
            allowed = False
            break
        if not allowed:
            continue

        groups[category["id"]] = tuple(sorted(related, key=lambda item: item["id"]))
        used.update(ids)

    units = []
    emitted_groups = set()
    for change in changes:
        if change["id"] not in used:
            units.append(("single", change, ()))
            continue
        category = next((item for item in changes if item["id"] in groups and change["id"] in {item["id"], *(child["id"] for child in groups[item["id"]])}), None)
        if category is None or category["id"] in emitted_groups:
            continue
        emitted_groups.add(category["id"])
        units.append(("group", category, groups[category["id"]]))
    return units


def render_group_notice(category_change, child_changes):
    count = len(child_changes)
    return (
        f"## 🕘 Change #{int(category_change['id'])} · Category removed\n"
        f"<t:{int(category_change['created_at'])}:f> · **Recorded**\n\n"
        f"**Category removed · {count} related channel moves.**\n\n"
        "Deleted history is not recoverable automatically.\n"
        "Use **Details** to review the affected resources."
    )


def can_undo(change):
    if change and change.get("action", "").startswith("observed_"):
        from services.server_change_observer import can_undo as observed_can_undo
        return observed_can_undo(change)
    return bool(change and change.get("reversible") and change.get("status") == "APPLIED"
                and change.get("action") in {"channel_update", "category_update", "message_delete"})


def _safe(value, limit=90):
    text = str(value if value is not None else "")
    return discord.utils.escape_mentions(discord.utils.escape_markdown(text))[:limit]


def _resource_name(change):
    before, after = change.get("before") or {}, change.get("after") or {}
    return after.get("name") or before.get("name") or {
        "guild": "Server settings",
        "message": "Managed bot message",
    }.get(change.get("resource_type"), "Unknown resource")


def _resource_label(change):
    name = _safe(_resource_name(change), 75)
    kind = change.get("resource_type")
    if kind == "channel":
        return f"#{name}"
    if kind == "category":
        return f"Category **{name}**"
    if kind == "role":
        return f"Role **{name}**"
    if kind == "guild":
        return f"Server **{name}**"
    return f"**{name}**"


def _summary(change):
    """One-line human summary for the owner-log channel."""
    action = change.get("action", "")
    before, after = change.get("before") or {}, change.get("after") or {}
    resource = _resource_label(change)

    if action.endswith("_delete") or action in {"channel_delete", "category_delete", "message_delete"}:
        if change.get("resource_type") == "category":
            return f"{resource} was deleted."
        if change.get("resource_type") == "message" or action == "message_delete":
            return f"{resource} was deleted."
        return f"{resource} was deleted."

    if action.endswith("_create"):
        return f"{resource} was created."

    changes = []
    fields = set(after.get("_fields", []))
    if before.get("name") != after.get("name") and before.get("name") is not None and after.get("name") is not None:
        changes.append(f"renamed from **{_safe(before['name'], 55)}**")
    if before.get("category_id") != after.get("category_id") and (
        "category_id" in fields or action in {"channel_update", "security_review", "offline_reconcile"}
    ):
        changes.append("moved to another category")
    if "topic" in fields:
        changes.append("topic changed")
    if "slowmode_delay" in fields:
        changes.append("slowmode changed")
    if "overwrites" in fields or "permissions" in fields:
        changes.append("permissions changed")
    if "nsfw" in fields:
        changes.append("age restriction changed")
    if change.get("resource_type") == "role" and "colour" in fields:
        changes.append("colour changed")
    if change.get("resource_type") == "role" and "mentionable" in fields:
        changes.append("mention setting changed")
    if change.get("resource_type") == "role" and "hoist" in fields:
        changes.append("display setting changed")

    if not changes:
        if action == "channel_restore":
            return f"{resource} was restored as a replacement."
        if action == "security_review":
            return f"{resource} changed and needs a security review."
        if action.endswith("_offline") or action == "offline_reconcile":
            return f"{resource} changed while GamerHQ was offline."
        return f"{resource} changed."

    if len(changes) == 1:
        return f"{resource}: {changes[0]}."
    return f"{resource}: {', '.join(changes[:2])}" + (f" and {len(changes)-2} more changes." if len(changes) > 2 else ".")


def render_notice(change):
    """Show the useful summary immediately; keep raw/history details behind owner review."""
    kind = {
        "channel_update": "Channel changed", "category_update": "Category changed",
        "channel_delete": "Channel removed", "category_delete": "Category removed",
        "message_delete": "Managed bot message removed", "channel_restore": "Replacement restored",
        "security_review": "Change requires security review", "offline_reconcile": "Offline change recorded",
    }.get(change.get("action"), "Server change recorded")
    if change.get("action", "").startswith("observed_"):
        _, resource, event = change["action"].split("_", 2)
        subject = {"channel": "Channel", "category": "Category", "role": "Role", "guild": "Server settings"}.get(resource, "Resource")
        kind = subject + {"update": " changed", "create": " created", "delete": " removed", "offline": " changed while offline"}.get(event, " changed")

    state = {"UNDOING": "Undo in progress", "UNDONE": "✅ Undone", "RESTORED": "♻️ Replacement restored",
             "REVIEW_REQUIRED": "⚠️ Review required"}.get(change.get("status"), "Recorded")
    summary = _summary(change)

    if can_undo(change):
        help_text = "↩️ **Undo available** — review and confirm if you want to restore the previous editable state."
    elif change.get("status") in {"UNDONE", "RESTORED"}:
        help_text = "This change has already been handled."
    elif change.get("action") in {"channel_delete", "category_delete"} or change.get("action", "").endswith("_delete"):
        help_text = "Deleted history is not recoverable automatically."
    else:
        help_text = "Automatic Undo is not available for this change."

    return (
        f"## 🕘 Change #{int(change['id'])} · {kind}\n"
        f"<t:{int(change['created_at'])}:f> · **{state}**\n\n"
        f"**{summary}**\n\n"
        f"{help_text}\n"
        "Use **Details** only for additional context."
    )


def change_for_message(guild, channel_id, message_id):
    raw = db.get_setting(message_key(guild.id, channel_id, message_id))
    if not raw or not str(raw).isdigit():
        return None
    notice = load_notice(guild.id, int(raw))
    if not notice or notice.get("state") != "SENT":
        return None
    if (notice.get("channel_id"), notice.get("message_id")) != (channel_id, message_id):
        return None
    change = structure.get_change(guild, int(raw))
    grouped_ids = notice.get("grouped_change_ids") or []
    if change and grouped_ids:
        grouped = []
        for change_id in grouped_ids:
            child = structure.get_change(guild, int(change_id))
            if child is not None:
                grouped.append(child)
        change = dict(change)
        change["grouped_changes"] = tuple(grouped)
    return change


async def _send_group(guild, channel, category_change, child_changes):
    old = load_notice(guild.id, category_change["id"])
    if old and old.get("state") != "PENDING":
        if old.get("state") == "SENDING":
            _store(guild.id, category_change["id"], dict(old, state="UNCERTAIN"))
            log.warning(
                "Grouped owner notice delivery uncertain; retained history, no resend guild=%s change=%s",
                guild.id, category_change["id"],
            )
        return True

    value = {
        "state": "SENDING",
        "channel_id": channel.id,
        "status": category_change["status"],
        "grouped_change_ids": [child["id"] for child in child_changes],
    }
    with db.connect() as conn:
        if old is None:
            claimed = conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                (notice_key(guild.id, category_change["id"]), json.dumps(value, sort_keys=True)),
            ).rowcount
        else:
            claimed = conn.execute(
                "UPDATE settings SET value=? WHERE key=? AND value=?",
                (
                    json.dumps(value, sort_keys=True),
                    notice_key(guild.id, category_change["id"]),
                    json.dumps(old, sort_keys=True),
                ),
            ).rowcount
        if not claimed:
            return True
        for child in child_changes:
            child_value = {
                "state": "GROUPED",
                "parent_change_id": category_change["id"],
                "status": child["status"],
            }
            conn.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                (notice_key(guild.id, child["id"]), json.dumps(child_value, sort_keys=True)),
            )

    from cogs.owner_changelog import ChangeNotice
    try:
        message = await channel.send(
            content=render_group_notice(category_change, child_changes),
            view=ChangeNotice(category_change),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as exc:
        definite = exc.status in (400, 403, 404, 429)
        _store(
            guild.id,
            category_change["id"],
            dict(value, state="PENDING" if definite else "UNCERTAIN"),
        )
        log.warning(
            "Grouped owner notice delivery incomplete guild=%s change=%s http=%s",
            guild.id, category_change["id"], exc.status,
        )
        return not definite
    except Exception:
        _store(guild.id, category_change["id"], dict(value, state="UNCERTAIN"))
        log.warning(
            "Grouped owner notice delivery uncertain guild=%s change=%s; no automatic resend",
            guild.id, category_change["id"],
        )
        return True

    value.update(state="SENT", message_id=message.id)
    with db.connect() as conn:
        conn.execute(
            "UPDATE settings SET value=? WHERE key=?",
            (json.dumps(value, sort_keys=True), notice_key(guild.id, category_change["id"])),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
            (message_key(guild.id, channel.id, message.id), str(category_change["id"])),
        )
    return True


async def _send(guild, channel, change):
    old = load_notice(guild.id, change["id"])
    if old and old.get("state") != "PENDING":
        if old.get("state") == "SENDING":
            _store(guild.id, change["id"], dict(old, state="UNCERTAIN"))
            log.warning("Owner notice delivery uncertain; retained history, no resend guild=%s change=%s", guild.id, change["id"])
        return True
    value = {"state": "SENDING", "channel_id": channel.id, "status": change["status"]}
    # Reserve before the HTTP call. A crash/uncertain response must not cause duplicate sends.
    with db.connect() as conn:
        if old is None:
            claimed = conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                                   (notice_key(guild.id, change["id"]), json.dumps(value, sort_keys=True))).rowcount
        else:
            claimed = conn.execute("UPDATE settings SET value=? WHERE key=? AND value=?",
                                   (json.dumps(value, sort_keys=True), notice_key(guild.id, change["id"]),
                                    json.dumps(old, sort_keys=True))).rowcount
    if not claimed:
        return True
    from cogs.owner_changelog import ChangeNotice
    try:
        message = await channel.send(content=render_notice(change), view=ChangeNotice(change),
                                     allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        definite = exc.status in (400, 403, 404, 429)
        _store(guild.id, change["id"], dict(value, state="PENDING" if definite else "UNCERTAIN"))
        log.warning("Owner notice delivery incomplete guild=%s change=%s http=%s", guild.id, change["id"], exc.status)
        return not definite
    except Exception:
        _store(guild.id, change["id"], dict(value, state="UNCERTAIN"))
        log.warning("Owner notice delivery uncertain guild=%s change=%s; no automatic resend", guild.id, change["id"])
        return True
    value.update(state="SENT", message_id=message.id)
    with db.connect() as conn:
        conn.execute("UPDATE settings SET value=? WHERE key=?",
                     (json.dumps(value, sort_keys=True), notice_key(guild.id, change["id"])))
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                     (message_key(guild.id, channel.id, message.id), str(change["id"])))
    return True


async def _refresh(guild, channel, change_id):
    state = load_notice(guild.id, change_id)
    change = structure.get_change(guild, change_id)
    if not state or not change or state.get("state") != "SENT" or state.get("status") == change["status"]:
        return
    if state.get("channel_id") != channel.id:
        return  # Never recreate old notices in a replacement channel.
    from cogs.owner_changelog import ChangeNotice
    try:
        message = await channel.fetch_message(int(state["message_id"]))
        if guild.me is None or message.author.id != guild.me.id:
            _store(guild.id, change_id, dict(state, state="INVALID"))
            return
        await message.edit(content=render_notice(change), view=ChangeNotice(change),
                           allowed_mentions=discord.AllowedMentions.none())
    except discord.NotFound:
        _store(guild.id, change_id, dict(state, state="DELETED"))
        return  # Respect deleted notices; history is still in the private review.
    except discord.HTTPException:
        return  # An edit can be safely retried; no new message is sent.
    _store(guild.id, change_id, dict(state, status=change["status"]))


async def flush(guild, *, include_latest=False):
    """Bounded delivery/recovery, no guild inventory or message history scans."""
    from cogs.owner_changelog import destination
    if destination(guild) is None:
        return
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        cursor = enable(guild, include_latest=include_latest)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM structure_change_log WHERE guild_id=? AND id>? ORDER BY id LIMIT ?",
                (guild.id, cursor, GROUP_LOOKAHEAD),
            ).fetchall()
        changes = [
            change for row in rows
            if (change := structure.get_change(guild, row["id"])) is not None
        ]
        delivered = 0
        for kind, change, grouped in _group_plan(guild, changes):
            if delivered >= BATCH_SIZE:
                break
            channel = destination(guild)  # Recheck privacy after awaits, never cache an unsafe destination.
            if channel is None:
                return
            if kind == "group":
                success = await _send_group(guild, channel, change, grouped)
                cursor_change_id = max([change["id"], *(child["id"] for child in grouped)])
            else:
                success = await _send(guild, channel, change)
                cursor_change_id = change["id"]
            if not success:
                break
            db.set_setting(cursor_key(guild.id), cursor_change_id)
            delivered += 1
        # Reconcile only terminal entries whose sent notice still has the previous status.
        # SQLite settings keys are indexed; message reads are targeted by saved ID.
        with db.connect() as conn:
            dirty = conn.execute(
                "SELECT c.id FROM structure_change_log c JOIN settings s ON s.key=(? || c.id) "
                "WHERE c.guild_id=? AND c.status IN ('UNDONE','RESTORED','REVIEW_REQUIRED','UNDOING') "
                "AND CASE WHEN json_valid(s.value) THEN json_extract(s.value,'$.state') END='SENT' "
                "AND CASE WHEN json_valid(s.value) THEN json_extract(s.value,'$.status') END!=c.status "
                "AND CASE WHEN json_valid(s.value) THEN json_extract(s.value,'$.channel_id') END=? ORDER BY c.id LIMIT ?",
                (f"owner_change_notice:{guild.id}:", guild.id, destination(guild).id if destination(guild) else 0, BATCH_SIZE),
            ).fetchall()
        for row in dirty:
            channel = destination(guild)
            if channel is None:
                return
            await _refresh(guild, channel, row["id"])
