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


def can_undo(change):
    return bool(change and change.get("reversible") and change.get("status") == "APPLIED"
                and change.get("action") in {"channel_update", "category_update", "message_delete"})


def render_notice(change):
    """Never interpolate resource names/IDs, actors, content or tokens into the channel."""
    kind = {
        "channel_update": "Channel changed", "category_update": "Category changed",
        "channel_delete": "Channel removed", "category_delete": "Category removed",
        "message_delete": "Managed bot message removed", "channel_restore": "Replacement restored",
        "security_review": "Change requires security review", "offline_reconcile": "Offline change recorded",
    }.get(change.get("action"), "Server change recorded")
    state = {"UNDONE": "✅ Undone", "RESTORED": "♻️ Replacement restored",
             "REVIEW_REQUIRED": "⚠️ Review required"}.get(change.get("status"), "Recorded")
    if can_undo(change):
        help_text = "Use **Undo** to review this change privately and confirm before reversing it."
    elif change.get("status") in {"UNDONE", "RESTORED"}:
        help_text = "This change has already been handled. Its private review remains available."
    elif change.get("action") in {"channel_delete", "category_delete"}:
        help_text = "Deletion cannot be undone. Review any available replacement option; deleted history is not recoverable."
    else:
        help_text = "Review this change privately. Automatic Undo is not available for this change."
    return (f"## 🕘 Change #{int(change['id'])} · {kind}\n"
            f"<t:{int(change['created_at'])}:f> · **{state}**\n\n{help_text}\n"
            "Details and actions are available only to the current server owner.")


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
                (guild.id, cursor, BATCH_SIZE),
            ).fetchall()
        for row in rows:
            channel = destination(guild)  # Recheck privacy after awaits, never cache an unsafe destination.
            if channel is None:
                return
            change = structure.get_change(guild, row["id"])
            if not await _send(guild, channel, change):
                break
            db.set_setting(cursor_key(guild.id), change["id"])
        # Reconcile only terminal entries whose sent notice still has the previous status.
        # SQLite settings keys are indexed; message reads are targeted by saved ID.
        with db.connect() as conn:
            dirty = conn.execute(
                "SELECT c.id FROM structure_change_log c JOIN settings s ON s.key=(? || c.id) "
                "WHERE c.guild_id=? AND c.status IN ('UNDONE','RESTORED') "
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
