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
    "public_updates_channel_id": "Community updates channel", "type": "Channel type",
    "default_auto_archive_duration": "Default thread archive time",
    "default_thread_slowmode_delay": "Default thread slow mode",
    "default_sort_order": "Default post order", "default_layout": "Default post layout",
    "flags": "Channel options", "managed": "Managed by an integration",
    "unicode_emoji": "Role emoji", "icon": "Icon", "banner": "Banner", "splash": "Invite image",
    "verification_level": "Member verification", "explicit_content_filter": "Media safety filter",
    "default_notifications": "Default notifications", "preferred_locale": "Community language",
    "system_channel_flags": "System message options", "features": "Server features",
    "premium_progress_bar_enabled": "Boost progress bar", "available_tags": "Forum tags",
}
GROUP_PAGE_SIZE = 8
PAGE_LIMIT = 1900


def units(value):
    return len(str(value).encode("utf-16-le")) // 2


def safe(value, limit=180):
    text = " ".join(str(value).split())
    text = discord.utils.escape_markdown(discord.utils.escape_mentions(text))
    if units(text) <= limit:
        return text
    kept, size = [], 0
    for char in text:
        size += units(char)
        if size > limit - 1:
            break
        kept.append(char)
    return "".join(kept).rstrip("\\") + "…"


def status(change):
    return STATUSES.get(change.get("status"), "Needs your review")


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
    """Resolve by identity and historical evidence, never a guessed display name."""
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
                state = json.loads(raw)
                name = state.get("name") if isinstance(state, dict) else None
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
    if field in {"slowmode_delay", "afk_timeout", "default_thread_slowmode_delay"}:
        return "Off" if value == 0 else f"{safe(value)} seconds"
    if field == "default_auto_archive_duration":
        return f"{safe(value)} minutes"
    if field == "user_limit" and value == 0:
        return "No limit"
    if field == "colour" and isinstance(value, int):
        return "Default" if value == 0 else f"#{value:06X}"
    if field == "bitrate" and isinstance(value, int):
        return f"{value // 1000} kbps"
    if field in {"icon", "banner", "splash"}:
        return "Configured"
    return safe(value)


def changed_fields(change):
    before, after = change.get("before", {}), change.get("after", {})
    if isinstance(after.get("_fields"), list):
        return [field for field in after["_fields"] if isinstance(field, str) and not field.startswith("_")]
    return sorted(field for field in set(before) | set(after)
                  if not field.startswith("_") and field not in {"id", "kind", "deleted"}
                  and before.get(field) != after.get(field))


def history_label(change):
    return safe(f"{generic_title(change)} · {resource_name(change)}", 100)


def _field_blocks(change, guild):
    before, after = change.get("before", {}), change.get("after", {})
    opaque = {"flags", "system_channel_flags", "features", "available_tags",
              "verification_level", "explicit_content_filter", "default_notifications",
              "default_sort_order", "default_layout", "type"}
    for field in changed_fields(change):
        if field in {"overwrites", "permissions"}:
            yield "**Permissions:** Access settings changed. Review the permissions in Discord."
        elif field not in FIELDS:
            yield "**Other setting:** Changed. The original values remain saved for technical review."
        elif field in opaque or any(isinstance(state.get(field), (dict, list)) for state in (before, after)):
            yield f"**{FIELDS[field]}:** Changed. Review this setting in Discord."
        elif field in {"topic", "description"} and any(units(state.get(field) or "") > 150 for state in (before, after)):
            # Preserve the full long description across pages, not a clipped JSON dump.
            for heading, state in (("Before", before), ("After", after)):
                text = " ".join(str(state.get(field) or "Not set").split())
                for index in range(0, len(text), 180):
                    part = safe(text[index:index + 180], 800)
                    continuation = " (continued)" if index else ""
                    yield f"**{FIELDS[field]} — {heading}{continuation}:** {part}"
        else:
            old = field_value(change, guild, field, before.get(field))
            new = field_value(change, guild, field, after.get(field))
            yield f"**{FIELDS[field]}:** {old} → {new}"


def detail_pages(change, guild=None, *, related=()):
    """Build bounded owner-only pages with status and safety guidance on EVERY page."""
    kind, verb = change.get("resource_type"), event(change)
    icon = {"category": "📁", "channel": "💬", "role": "🏷️", "guild": "⚙️"}.get(kind, "🕘")
    header = f"# {icon} {generic_title(change)}\n**{resource_name(change)}**\n\n"
    if verb == "deleted":
        intro = ("The category was deleted." if kind == "category" else
                 "This item was deleted. A replacement cannot recover its original identity or history.")
        blocks = [intro]
    elif verb == "created":
        blocks = ["This item was created."]
    else:
        blocks = list(_field_blocks(change, guild)) or ["The saved configuration changed."]
    if verb == "offline":
        blocks.insert(0, "Difference noticed after reconnecting; this is not a reconstruction of every offline action.")
    rows = list({row["resource_id"]: row for row in related}.values())
    if rows:
        header += f"**Related channel moves ({len(rows)}):**\n"
        blocks = [f"• {resource_name(row)} → No category" for row in rows]
    from services.owner_change_feed import can_undo
    actor = guild.get_member(change.get("actor_id")) if guild is not None and change.get("actor_id") else None
    actor_name = getattr(actor, "display_name", None)
    footer = f"\n\n**Status:** {status(change)}\n**Changed by:** " + (
        safe(actor_name, 80) + " (current name)" if isinstance(actor_name, str) else "Unknown")
    if rows:
        footer += "\nThese records concern category assignments, not deletion of the channels or their messages."
    if can_undo(change):
        footer += "\nReview the change, then confirm Undo to restore the previous settings."
    elif verb == "deleted" and kind == "category":
        footer += "\nUndo is not available for category deletion."
    elif change.get("status") == "UNDONE":
        footer += "\nThis change has already been undone."
    else:
        footer += "\nAutomatic Undo is not available for this change."
    footer += f"\nChange #{change['id']}"
    budget = PAGE_LIMIT - units(header + footer) - 60
    pages, current, used = [], [], 0
    for block in blocks:
        if current and (used + units(block) + 1 > budget or (rows and len(current) >= GROUP_PAGE_SIZE)):
            pages.append(current)
            current, used = [], 0
        current.append(block)
        used += units(block) + 1
    pages.append(current)
    count = len(pages)
    return [header + "\n".join(body) + footer + (f"\nPage {index + 1} of {count}" if count > 1 else "")
            for index, body in enumerate(pages)]


def details(change, guild=None, *, related=(), page=0):
    pages = detail_pages(change, guild, related=related)
    return pages[min(max(0, page), len(pages) - 1)]
