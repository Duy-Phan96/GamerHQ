"""Plain-language presentation for the existing owner history.

Detailed renderers are called only after owner authorization. Public notices must
use generic_title/status only, never resource_name/details or historical names.
This module reads stored observations; it never changes managed desired state.
"""
from __future__ import annotations

import json

import discord
from database import db

STATUSES = {
    "APPLIED": "Recorded", "UNDOING": "Undo in progress", "UNDONE": "Undone",
    "RESTORED": "Replacement created", "REVIEW_REQUIRED": "Needs your review",
}
FIELDS = {
    "name": "Name", "category_id": "Category", "position": "Order",
    "topic": "Description", "nsfw": "Age restriction", "slowmode_delay": "Slow mode",
    "bitrate": "Voice quality", "user_limit": "Voice user limit", "rtc_region": "Voice region",
    "colour": "Colour", "hoist": "Shown separately in the member list",
    "mentionable": "Can be mentioned", "description": "Description",
    "afk_timeout": "Inactive voice timeout", "afk_channel_id": "Inactive voice channel",
    "system_channel_id": "System messages channel", "rules_channel_id": "Rules channel",
    "public_updates_channel_id": "Community updates channel",
}
GROUP_PAGE_SIZE = 8


def safe(value, limit=180):
    text = " ".join(str(value).split())
    # Keep labels on one line and prevent formatting/mentions in private replies.
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text[:limit]))


def status(change):
    return STATUSES.get(change.get("status"), "Recorded")


def event(change):
    action = change.get("action", "")
    if action.endswith("_delete"):
        return "deleted"
    if action.endswith("_create"):
        return "created"
    if action.endswith("_offline") or action == "offline_reconcile":
        return "offline"
    return "updated"


def generic_title(change):
    subject = {"category": "Category", "channel": "Channel", "role": "Role",
               "guild": "Server settings", "message": "Managed message"}.get(
                   change.get("resource_type"), "Server configuration")
    suffix = {"deleted": "removed", "created": "created", "offline": "changed while offline"}.get(event(change), "changed")
    return f"{subject} {suffix}"


def resource_name(change):
    for state in (change.get("after", {}), change.get("before", {})):
        name = state.get("name")
        if isinstance(name, str) and name.strip():
            return safe(name, 100)
    return "Name unavailable"


def _stored_name(guild_id, resource_id, change):
    """Resolve by identity and historical evidence, never by a guessed name.

    A deletion arriving just after a child move still has the parent's old name.
    Legacy rows need no migration; lookups are bounded and scoped to this guild.
    """
    if not resource_id:
        return None
    try:
        with db.connect() as conn:
            following = conn.execute(
                "SELECT before_json FROM structure_change_log WHERE guild_id=? "
                "AND resource_id=? AND id>=? ORDER BY id LIMIT 1",
                (guild_id, resource_id, change["id"]),
            ).fetchone()
            preceding = conn.execute(
                "SELECT before_json,after_json FROM structure_change_log WHERE guild_id=? "
                "AND resource_id=? AND id<? ORDER BY id DESC LIMIT 1",
                (guild_id, resource_id, change["id"]),
            ).fetchone()
            candidates = [following["before_json"]] if following else []
            if preceding:
                candidates += [preceding["after_json"], preceding["before_json"]]
            for raw in candidates:
                name = json.loads(raw).get("name")
                if isinstance(name, str) and name.strip():
                    return safe(name, 100)
    except (ValueError, TypeError, KeyError):
        return None
    return None


def reference_name(change, guild, field, value):
    if value is None:
        return "No category" if field == "category_id" else "Not set"
    historical = _stored_name(change["guild_id"], value, change)
    if historical:
        return historical
    # A live fallback is explicitly labelled: it may have been renamed later.
    resource = guild.get_channel(value) if guild is not None else None
    name = getattr(resource, "name", None)
    if isinstance(name, str) and name.strip():
        return safe(name, 100) + " (current name)"
    return "Name unavailable"


def field_value(change, guild, field, value):
    if field.endswith("_id"):
        return reference_name(change, guild, field, value)
    if value is None or value == "":
        return "Automatic" if field == "rtc_region" else "Not set"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if field in {"slowmode_delay", "afk_timeout"}:
        return "Off" if value == 0 else f"{safe(value)} seconds"
    if field == "user_limit" and value == 0:
        return "No limit"
    if field == "colour" and isinstance(value, int):
        return "Default" if value == 0 else f"#{value:06X}"
    if field == "bitrate" and isinstance(value, int):
        return f"{value // 1000} kbps"
    return safe(value)


def changed_fields(change):
    before, after = change.get("before", {}), change.get("after", {})
    if isinstance(after.get("_fields"), list):
        return [field for field in after["_fields"] if not field.startswith("_")]
    return sorted(field for field in set(before) | set(after)
                  if not field.startswith("_") and field not in {"id", "kind", "deleted"}
                  and before.get(field) != after.get(field))


def history_label(change):
    return f"{generic_title(change)} · {resource_name(change)}"[:100]


def details(change, guild=None, *, related=(), page=0):
    """Owner-only text. No raw resource IDs, JSON, enum names or internal keys."""
    kind = change.get("resource_type")
    icon = {"category": "📁", "channel": "💬", "role": "🏷️", "guild": "⚙️"}.get(kind, "🕘")
    lines = [f"# {icon} {generic_title(change)}", f"**{resource_name(change)}**", ""]
    before, after = change.get("before", {}), change.get("after", {})
    verb = event(change)
    if verb == "deleted":
        lines.append("The category was deleted." if kind == "category" else
                     "This item was deleted. Creating a replacement would not restore its original identity or history.")
    elif verb == "created":
        lines.append("This item was created.")
    else:
        fields = changed_fields(change)
        shown = 0
        other = False
        for field in fields:
            if field in {"overwrites", "permissions"}:
                lines.append("**Permissions:** Access settings changed. Review the permissions in Discord.")
                shown += 1
            elif field in FIELDS and shown < 6:
                old = field_value(change, guild, field, before.get(field))
                new = field_value(change, guild, field, after.get(field))
                lines.append(f"**{FIELDS[field]}:** {old} → {new}")
                shown += 1
            else:
                other = True
        if other:
            lines.append("Additional settings changed; the complete record is saved in the change history.")
        if verb == "offline":
            lines.append("This is a difference noticed after reconnecting, not a reconstruction of every action while offline.")
    if related:
        # Count resources, not duplicate low-level events; individual rows remain intact.
        unique = {row["resource_id"]: row for row in related}
        rows = list(unique.values())
        pages = max(1, (len(rows) + GROUP_PAGE_SIZE - 1) // GROUP_PAGE_SIZE)
        page = min(max(0, page), pages - 1)
        lines += ["", f"**Related channel moves ({len(rows)}):**"]
        for row in rows[page * GROUP_PAGE_SIZE:(page + 1) * GROUP_PAGE_SIZE]:
            lines.append(f"• {resource_name(row)} → No category")
        if pages > 1:
            lines.append(f"Page {page + 1} of {pages}")
        lines.append("These records concern category assignments, not deletion of the channels or their messages.")
    from services.owner_change_feed import can_undo
    lines += ["", f"**Status:** {status(change)}"]
    actor = guild.get_member(change.get("actor_id")) if guild is not None and change.get("actor_id") else None
    actor_name = getattr(actor, "display_name", None)
    lines.append("**Changed by:** " + (safe(actor_name, 80) if isinstance(actor_name, str) else "Not available"))
    if can_undo(change):
        lines.append("Review the change, then confirm Undo to restore the previous settings.")
    elif verb == "deleted" and kind == "category":
        lines.append("Undo is not available for category deletion.")
    elif change.get("status") == "UNDONE":
        lines.append("This change has already been undone.")
    else:
        lines.append("Automatic Undo is not available for this change.")
    lines.append(f"Change #{change['id']}")
    return "\n".join(lines)[:1950]
