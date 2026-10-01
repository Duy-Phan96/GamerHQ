"""Owner notices backed by the existing SQLite history and delivery records.

Category deletions may group narrowly matching child moves. Grouping changes only
presentation; the individual rows and their original before/after values remain.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import discord
from database import db
from services import structure_adoption_service as structure
from services import owner_change_display as display

log = logging.getLogger(__name__)
BATCH_SIZE = 10
GROUP_WINDOW = 3.0
GROUP_SCAN_LIMIT = 100
_locks = {}
_deferred = {}


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


def can_undo(change):
    if change and change.get("action", "").startswith("observed_"):
        from services.server_change_observer import can_undo as observed_can_undo
        return observed_can_undo(change)
    return bool(change and change.get("reversible") and change.get("status") == "APPLIED"
                and change.get("action") in {"channel_update", "category_update", "message_delete"})


def _category_deleted(change):
    return bool(change and change.get("status") == "APPLIED"
                and change.get("resource_type") == "category"
                and change.get("action") in {"category_delete", "observed_category_delete"})


def _detached(change):
    """Never absorb rename, permission, topic or other concurrent edits."""
    if (change.get("status") != "APPLIED" or change.get("resource_type") != "channel"
            or change.get("action") not in {"channel_update", "observed_channel_update"}):
        return False
    before, after = change["before"], change["after"]
    fields = {field for field in set(before) | set(after)
              if not field.startswith("_") and field != "id" and before.get(field) != after.get(field)}
    return bool(before.get("category_id") and "category_id" in after and after["category_id"] is None
                and "category_id" in fields and fields <= {"category_id", "position"})


def related_changes(guild, change_id):
    """Only the root's persisted, same-guild members are exposed in private review."""
    notice = load_notice(guild.id, change_id) or {}
    ids = notice.get("members", [])
    if not isinstance(ids, list):
        return []
    rows = []
    for value in ids[:GROUP_SCAN_LIMIT]:
        if not isinstance(value, int):
            continue
        row = structure.get_change(guild, value)
        if row is not None:
            rows.append(row)
    return rows


def _prepare_group(guild, change, cursor):
    """Return a short wait, or freeze an unsent group in one transaction.

    Identity + a narrow time window + a pure category detach are all required.
    Already sent/uncertain notices are never regrouped or deleted. Missing or
    ambiguous parent events fall back to individual delivery, not suppression.
    """
    if load_notice(guild.id, change["id"]) is not None:
        return 0.0
    if not (_category_deleted(change) or _detached(change)):
        return 0.0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM structure_change_log WHERE guild_id=? AND id>? ORDER BY id LIMIT ?",
            (guild.id, cursor, GROUP_SCAN_LIMIT),
        ).fetchall()
    candidates = [structure._change_row(row) for row in rows]
    parent_id = change["resource_id"] if _category_deleted(change) else change["before"]["category_id"]
    roots = [row for row in candidates if _category_deleted(row) and row["resource_id"] == parent_id
             and abs(row["created_at"] - change["created_at"]) <= GROUP_WINDOW
             and load_notice(guild.id, row["id"]) is None]
    root = roots[0] if len(roots) == 1 else None
    due = (root or change)["created_at"] + GROUP_WINDOW
    delay = max(0.0, due - time.time())
    if delay:
        return delay
    if root is None:
        return 0.0
    members = [row for row in candidates if _detached(row)
               and row["before"]["category_id"] == root["resource_id"]
               and abs(row["created_at"] - root["created_at"]) <= GROUP_WINDOW
               and load_notice(guild.id, row["id"]) is None]
    if not members:
        return 0.0
    ids = [root["id"], *(row["id"] for row in members)]
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for change_id in ids:
            if conn.execute("SELECT 1 FROM settings WHERE key=?", (notice_key(guild.id, change_id),)).fetchone():
                return 0.0
            current = conn.execute("SELECT status FROM structure_change_log WHERE guild_id=? AND id=?",
                                   (guild.id, change_id)).fetchone()
            if not current or current["status"] != "APPLIED":
                return 0.0
        root_value = {"state": "PENDING", "status": root["status"],
                      "members": [row["id"] for row in members],
                      "channel_count": len({row["resource_id"] for row in members})}
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?)",
                     (notice_key(guild.id, root["id"]), json.dumps(root_value, sort_keys=True)))
        for row in members:
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?)",
                         (notice_key(guild.id, row["id"]), json.dumps(
                             {"state": "GROUPED", "group_id": root["id"]}, sort_keys=True)))
    return 0.0


def _schedule_flush(guild, delay):
    pending = _deferred.get(guild.id)
    if pending is not None and not pending.done():
        return

    async def later():
        try:
            await asyncio.sleep(max(0.05, delay))
            # Allow a new bounded wait if the parent event arrived after a child.
            _deferred.pop(guild.id, None)
            await flush(guild)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Owner grouped delivery deferred guild=%s; periodic recovery remains active", guild.id)
        finally:
            if _deferred.get(guild.id) is asyncio.current_task():
                _deferred.pop(guild.id, None)
    _deferred[guild.id] = asyncio.create_task(later())


def cancel_pending_flushes():
    for pending in _deferred.values():
        pending.cancel()
    _deferred.clear()


def render_notice(change, *, related_count=0):
    """No names, resource IDs, actors, field values or tokens in channel messages."""
    kind = display.generic_title(change)
    state = display.status(change)
    if change.get("status") == "UNDONE":
        state = "✅ Undone"
    if related_count and _category_deleted(change):
        help_text = (f"A category was deleted. **{related_count} related channel moves** were recorded.\n"
                     "Open the private details to see the affected channels.")
    elif can_undo(change):
        help_text = "Use **Undo** to review this change privately before confirming."
    elif change.get("status") in {"UNDONE", "RESTORED"}:
        help_text = "This change has already been handled. Its private review remains available."
    elif display.event(change) == "deleted":
        help_text = "Deletion cannot be undone; deleted history is not recoverable. Open the private details for this record."
    else:
        help_text = "Open the private details to see what changed. Automatic Undo is not available."
    return (f"## 🕘 {kind}\n<t:{int(change['created_at'])}:f> · **{state}**\n\n{help_text}\n"
            f"Details and actions are available only to the current server owner. · Change #{int(change['id'])}")


def change_for_message(guild, channel_id, message_id):
    raw = db.get_setting(message_key(guild.id, channel_id, message_id))
    if not raw or not str(raw).isdigit():
        return None
    notice = load_notice(guild.id, int(raw))
    if not notice or notice.get("state") != "SENT":
        return None
    if (notice.get("channel_id"), notice.get("message_id")) != (channel_id, message_id):
        return None
    return structure.get_change(guild, int(raw))


async def _send(guild, channel, change):
    old = load_notice(guild.id, change["id"])
    if old and old.get("state") == "GROUPED":
        root_id = old.get("group_id")
        root = structure.get_change(guild, root_id) if isinstance(root_id, int) else None
        root_notice = load_notice(guild.id, root_id) if root else None
        if (not root_notice or root_notice.get("state") == "GROUPED"
                or change["id"] not in root_notice.get("members", [])):
            log.warning("Owner group binding needs review guild=%s change=%s", guild.id, change["id"])
            return False
        return await _send(guild, channel, root)
    if old and old.get("state") != "PENDING":
        if old.get("state") == "SENDING":
            _store(guild.id, change["id"], dict(old, state="UNCERTAIN"))
            log.warning("Owner notice delivery uncertain; retained history, no resend guild=%s change=%s", guild.id, change["id"])
        return True
    value = dict(old or {}, state="SENDING", channel_id=channel.id, status=change["status"])
    # Reserve before HTTP. A crash/uncertain response must not duplicate sends.
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
        message = await channel.send(
            content=render_notice(change, related_count=value.get("channel_count", 0)),
            view=ChangeNotice(change), allowed_mentions=discord.AllowedMentions.none())
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
        await message.edit(content=render_notice(change, related_count=state.get("channel_count", 0)),
                           view=ChangeNotice(change), allowed_mentions=discord.AllowedMentions.none())
    except discord.NotFound:
        _store(guild.id, change_id, dict(state, state="DELETED"))
        return
    except discord.HTTPException:
        return  # Retry the edit, not a new message.
    _store(guild.id, change_id, dict(state, status=change["status"]))


async def flush(guild, *, include_latest=False):
    """Bounded delivery/recovery, no guild inventory or message-history scans."""
    from cogs.owner_changelog import destination
    if destination(guild) is None:
        return
    async with _locks.setdefault(guild.id, asyncio.Lock()):
        cursor = enable(guild, include_latest=include_latest)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM structure_change_log WHERE guild_id=? AND id>? ORDER BY id LIMIT ?",
                (guild.id, cursor, BATCH_SIZE),
            ).fetchall()
        for row in rows:
            channel = destination(guild)
            if channel is None:
                return
            change = structure.get_change(guild, row["id"])
            delay = _prepare_group(guild, change, cursor)
            if delay:
                _schedule_flush(guild, delay)
                break
            if not await _send(guild, channel, change):
                break
            db.set_setting(cursor_key(guild.id), change["id"])
            cursor = change["id"]
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
