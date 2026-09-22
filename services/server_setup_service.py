from __future__ import annotations

from dataclasses import dataclass
import re

import discord


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    kind: str = "text"  # text | voice


@dataclass(frozen=True)
class CategorySpec:
    name: str
    channels: tuple[ChannelSpec, ...]
    private: bool = False


SERVER_BLUEPRINT: tuple[CategorySpec, ...] = (
    CategorySpec(
        "👋 START HERE",
        (
            ChannelSpec("👋・welcome"),
            ChannelSpec("📜・rules"),
            ChannelSpec("📢・announcements"),
            ChannelSpec("🎮・choose-your-games"),
            ChannelSpec("👤・choose-your-roles"),
            ChannelSpec("🎯・looking-for-group"),
            ChannelSpec("📘・guide"),
            ChannelSpec("🆘・need-support"),
            ChannelSpec("💜・support-gamerhq"),
        ),
    ),
    CategorySpec(
        "💬 COMMUNITY",
        (
            ChannelSpec("💬・general"),
            ChannelSpec("👋・newbies"),
            ChannelSpec("👋・introductions"),

            ChannelSpec("🤖・bot-commands"),
            ChannelSpec("💡・suggestions"),
        ),
    ),
    CategorySpec("🤝 PARTNERS & BENEFITS", (ChannelSpec("💜・direct-support"), ChannelSpec("🛒・amazon"), ChannelSpec("🇩🇪・haushaltscheck"), ChannelSpec("🎮・gaming-deals"), ChannelSpec("🤖・ai-tools"))),
    CategorySpec("🎫 SUPPORT TICKETS", (), private=True),
    CategorySpec("🏆 EVENTS", (ChannelSpec("🏆・tournaments"), ChannelSpec("🎁・giveaways"))),
    CategorySpec(
        "🔊 VOICE CHANNELS",
        (
            ChannelSpec("🔊 Chill Lounge", "voice"),
            ChannelSpec("➕ Create Voice", "voice"),
            ChannelSpec("😴 AFK", "voice"),
        ),
    ),
    CategorySpec(
        "🔒 STAFF",
        (
            ChannelSpec("💬・staff-chat"),
            ChannelSpec("💡・staff-suggestions"),
            ChannelSpec("🎫・ticket-logs"),
            ChannelSpec("🚨・mod-log"),
            ChannelSpec("🤖・bot-log"),
            ChannelSpec("🛠️・mod-commands"),
        ),
        private=True,
    ),
)


def normalize_name(name: str) -> str:
    value = name.casefold().replace("・", "-")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _find_category(guild: discord.Guild, spec: CategorySpec) -> discord.CategoryChannel | None:
    wanted = normalize_name(spec.name)
    for category in guild.categories:
        if normalize_name(category.name) == wanted:
            return category
    return None


def _find_channel(category: discord.CategoryChannel, spec: ChannelSpec):
    wanted = normalize_name(spec.name)
    candidates = category.voice_channels if spec.kind == "voice" else category.text_channels
    for channel in candidates:
        if normalize_name(channel.name) == wanted:
            return channel
    return None


def analyze_server(guild: discord.Guild) -> dict:
    categories = []
    missing_categories = 0
    missing_channels = 0

    for spec in SERVER_BLUEPRINT:
        category = _find_category(guild, spec)
        channel_rows = []
        if category is None:
            missing_categories += 1
            missing_channels += len(spec.channels)
            for channel_spec in spec.channels:
                channel_rows.append({"spec": channel_spec, "channel": None})
        else:
            for channel_spec in spec.channels:
                channel = _find_channel(category, channel_spec)
                if channel is None:
                    missing_channels += 1
                channel_rows.append({"spec": channel_spec, "channel": channel})
        categories.append({"spec": spec, "category": category, "channels": channel_rows})

    # Optional modules are reported only; /server setup does not create or delete them.
    streamer_category = next((c for c in guild.categories if normalize_name(c.name) == "streamers"), None)
    game_categories = []
    try:
        from database import db
        for game in db.get_area_games():
            category_id = game.get("category_id")
            if category_id and guild.get_channel(int(category_id)):
                game_categories.append(game)
    except Exception:
        game_categories = []

    return {
        "categories": categories,
        "missing_categories": missing_categories,
        "missing_channels": missing_channels,
        "streamer_installed": streamer_category is not None,
        "managed_games": len(game_categories),
        "complete": missing_categories == 0 and missing_channels == 0,
    }


def render_summary(guild: discord.Guild, report: dict) -> str:
    lines = [
        "# ⚙️ GamerHQ Server Setup",
        "",
        "**Owner-only setup & repair. Nothing changes until you confirm.**",
        "",
    ]
    for row in report["categories"]:
        spec: CategorySpec = row["spec"]
        category = row["category"]
        missing = sum(1 for ch in row["channels"] if ch["channel"] is None)
        if category is None:
            status = f"❌ Missing (+{len(spec.channels)} channels)"
        elif missing:
            status = f"⚠️ {missing} channel{'s' if missing != 1 else ''} missing"
        else:
            status = "✅ Complete"
        lines.append(f"**{spec.name}** — {status}")

    lines.extend([
        "",
        f"🎮 **GAME SYSTEM** — {report['managed_games']} managed game categories detected",
        f"🎥 **STREAMER SYSTEM** — {'✅ Installed' if report['streamer_installed'] else 'ℹ️ Not installed'}",
        "",
    ])
    if report["complete"]:
        lines.append("✅ **Core GamerHQ server structure is complete.**")
    else:
        lines.append(
            "🔧 The inventory shows missing resources. This update repairs onboarding, LFG/guide boards, EVENTS and private staff suggestions. Missing unrelated resources are reported."
        )
    lines.extend([
        "",
        "Setup organizes the core boards and EVENTS, publishes the central guide, and configures private suggestions. It also repairs Support and PARTNERS & BENEFITS with read-only channels and separate messages, and refreshes existing Music Bots access.",
    ])
    return "\n".join(lines)


def render_details(report: dict) -> str:
    lines = ["# 🔍 GamerHQ Setup Details", ""]
    for row in report["categories"]:
        spec: CategorySpec = row["spec"]
        category = row["category"]
        lines.append(f"## {spec.name}")
        lines.append("✅ Category found" if category else "⚠️ Category missing (inventory only)")
        for ch in row["channels"]:
            channel_spec: ChannelSpec = ch["spec"]
            if ch["channel"]:
                lines.append(f"✅ {channel_spec.name}")
            else:
                icon = "🔊" if channel_spec.kind == "voice" else "#️⃣"
                lines.append(f"⚠️ {icon} {channel_spec.name} — missing")
        lines.append("")
    lines.append("**Update preserves welcome/newbies history; moves LFG to START HERE and tournaments/giveaways to EVENTS; maintains guide, suggestions and bot-command pins; creates a private inbox in existing STAFF. Only recognized obsolete bot guides are removed. Repair also deletes recorded legacy finanzberatung only after full content, thread and dependency checks; uncertain cases receive an exact MANUAL_REVIEW reason.**")
    return "\n".join(lines)


async def repair_server(guild: discord.Guild, bot=None) -> tuple[list[str], list[str]]:
    """Focused onboarding update. Unrelated categories/resources remain untouched."""
    from services.onboarding_service import migrate_onboarding
    return await migrate_onboarding(guild, bot)


async def migrate_v27_community_and_game_channels(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """One-way V27 cleanup.

    - Rename the two legacy COMMUNITY channels in place (preserves manual position/history).
    - Remove only DB-linked game memes channels.
    - Never moves channels or touches unrelated/custom channels.
    """
    changed: list[str] = []
    failed: list[str] = []

    community = next((c for c in guild.categories if normalize_name(c.name) == "community"), None)
    if community:
        renames = {
            "memes": "🏆・tournaments",
            "clips-and-highlights": "🎁・giveaways",
        }
        for old_alias, new_name in renames.items():
            # If target already exists, leave legacy channel untouched rather than risk a duplicate merge.
            target_alias = normalize_name(new_name)
            if any(normalize_name(ch.name) == target_alias for ch in community.text_channels):
                continue
            old = next((ch for ch in community.text_channels if normalize_name(ch.name) == old_alias), None)
            if old:
                try:
                    await old.edit(name=new_name, reason="GamerHQ V27 community channel migration")
                    changed.append(f"Renamed {old_alias} → {new_name}")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    failed.append(f"Rename {old_alias}: {exc}")

    try:
        from database import db
        for game in db.get_area_games():
            memes_id = game.get("memes_channel_id")
            if not memes_id:
                continue
            channel = guild.get_channel(int(memes_id))
            # Delete only the channel explicitly linked as this game's legacy memes channel.
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    await channel.delete(reason=f"GamerHQ V27: remove legacy game memes channel ({game['name']})")
                    changed.append(f"Removed game memes: {game['name']}")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    failed.append(f"Game memes {game['name']}: {exc}")
                    continue
            db.clear_game_memes_channel(game["id"])
    except Exception as exc:
        failed.append(f"Game memes migration: {exc}")

    return changed, failed
