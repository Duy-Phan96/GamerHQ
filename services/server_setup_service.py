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
        ),
    ),
    CategorySpec(
        "💬 COMMUNITY",
        (
            ChannelSpec("💬・general"),
            ChannelSpec("👋・introductions"),
            ChannelSpec("🏆・tournaments"),
            ChannelSpec("🎁・giveaways"),
            ChannelSpec("🎮・looking-for-group"),
            ChannelSpec("📘・community-commands"),
            ChannelSpec("💡・suggestions"),
        ),
    ),
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
            f"🔧 Repair can create **{report['missing_categories']} missing categor{'y' if report['missing_categories'] == 1 else 'ies'}** "
            f"and **{report['missing_channels']} missing channel{'s' if report['missing_channels'] != 1 else ''}**."
        )
    lines.extend([
        "",
        "Custom areas (for example KAMEX), game categories and unknown channels are **never deleted or renamed** by this setup.",
    ])
    return "\n".join(lines)


def render_details(report: dict) -> str:
    lines = ["# 🔍 GamerHQ Setup Details", ""]
    for row in report["categories"]:
        spec: CategorySpec = row["spec"]
        category = row["category"]
        lines.append(f"## {spec.name}")
        lines.append("✅ Category found" if category else "➕ Category will be created")
        for ch in row["channels"]:
            channel_spec: ChannelSpec = ch["spec"]
            if ch["channel"]:
                lines.append(f"✅ {channel_spec.name}")
            else:
                icon = "🔊" if channel_spec.kind == "voice" else "#️⃣"
                lines.append(f"➕ {icon} {channel_spec.name}")
        lines.append("")
    lines.append("**No deletes, renames or moves are performed by Repair.**")
    return "\n".join(lines)


async def repair_server(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """Create only missing core resources. Existing resources are adopted by normalized name.

    Returns (created, failed). This intentionally does not delete, rename, move or rewrite
    existing resources in V1.
    """
    created: list[str] = []
    failed: list[str] = []

    for spec in SERVER_BLUEPRINT:
        category = _find_category(guild, spec)
        if category is None:
            try:
                overwrites = None
                if spec.private:
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    }
                    if guild.me:
                        overwrites[guild.me] = discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=True,
                            manage_channels=True,
                            manage_messages=True,
                        )
                    owner = guild.get_member(guild.owner_id)
                    if owner:
                        overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
                category = await guild.create_category(spec.name, overwrites=overwrites, reason="GamerHQ owner server setup")
                created.append(f"Category: {spec.name}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                failed.append(f"Category: {spec.name} ({exc})")
                continue

        for channel_spec in spec.channels:
            if _find_channel(category, channel_spec) is not None:
                continue
            try:
                if channel_spec.kind == "voice":
                    await category.create_voice_channel(channel_spec.name, reason="GamerHQ owner server setup")
                else:
                    await category.create_text_channel(channel_spec.name, reason="GamerHQ owner server setup")
                created.append(f"{spec.name} → {channel_spec.name}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                failed.append(f"{spec.name} → {channel_spec.name} ({exc})")

    return created, failed


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
