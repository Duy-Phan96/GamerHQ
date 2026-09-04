import discord
from discord import app_commands

from database import db
from services.server_service import ServerMessageError, upsert_fixed_message


STAFF_COMMAND_ROOTS = ("server", "game-admin")
STAFF_GUIDE_CHANNEL_KEY = "server_staff_commands_channel_id"
STAFF_GUIDE_MESSAGE_KEY = "server_staff_commands_message_id"


def _clean_description(description: str | None) -> str:
    text = (description or "No description available.").strip()
    if text.lower().startswith("admin:"):
        text = text[6:].strip()
    return text


def _parameter_hint(command: app_commands.Command) -> str:
    parts: list[str] = []
    for parameter in command.parameters:
        token = f"<{parameter.name}>" if parameter.required else f"[{parameter.name}]"
        parts.append(token)
    return " " + " ".join(parts) if parts else ""


def _find_group(bot, guild: discord.Guild, name: str):
    # Development uses guild-sync, so prefer the guild command tree.
    for command in bot.tree.get_commands(guild=guild):
        if command.name == name:
            return command
    # Fallback for local/global registration while the tree is being prepared.
    for command in bot.tree.get_commands():
        if command.name == name:
            return command
    return None


def build_staff_command_guide(bot, guild: discord.Guild) -> str:
    lines = [
        "# 🛠️ GamerHQ Staff Commands",
        "",
        "Official command reference for GamerHQ staff.",
        "",
        "🔒 **The commands listed here are Administrator-only.**",
        "The guide is generated from the bot's registered staff commands and refreshes automatically after bot restarts.",
        "Game model: `create/delete` manage the Game Library; `add-area/remove-area` manage only Discord areas.",
        "",
    ]

    section_titles = {
        "game-admin": "## 🎮 GAME MANAGEMENT",
        "server": "## ⚙️ SERVER MANAGEMENT",
    }

    found_any = False
    for root_name in STAFF_COMMAND_ROOTS:
        group = _find_group(bot, guild, root_name)
        if group is None or not isinstance(group, app_commands.Group):
            continue
        found_any = True
        lines.extend([section_titles.get(root_name, f"## /{root_name}"), ""])
        for command in sorted(group.commands, key=lambda c: c.name):
            if isinstance(command, app_commands.Group):
                # Current GamerHQ staff commands are one level deep. Keep nested
                # groups readable if we add one later.
                for nested in sorted(command.commands, key=lambda c: c.name):
                    usage = f"/{root_name} {command.name} {nested.name}"
                    if isinstance(nested, app_commands.Command):
                        usage += _parameter_hint(nested)
                    lines.append(f"### `{usage}`")
                    lines.append(_clean_description(nested.description))
                    lines.append("")
                continue

            usage = f"/{root_name} {command.name}"
            if isinstance(command, app_commands.Command):
                usage += _parameter_hint(command)
            lines.append(f"### `{usage}`")
            lines.append(_clean_description(command.description))
            lines.append("")

    if not found_any:
        lines.extend([
            "⚠️ No staff command groups were found in the current command tree.",
            "Restart the bot after command sync and refresh this guide.",
            "",
        ])

    lines.extend([
        "---",
        "💡 Use the interactive previews and confirmation buttons before destructive actions.",
        "This reference is maintained by **GamerHQ Bot**.",
    ])
    return "\n".join(lines).strip()


def _alias(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _staff_role_overwrites(guild: discord.Guild) -> dict:
    """Private by default; expose to roles that already carry staff permissions."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        perms = role.permissions
        if perms.administrator or perms.manage_guild or perms.manage_messages or perms.moderate_members:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
            )
    return overwrites


async def ensure_staff_guide_channel(guild: discord.Guild) -> discord.TextChannel:
    """Resolve the managed mod-command channel, or create it safely in the staff area."""
    # Only reuse a stored channel when it is actually the dedicated command guide.
    # Older builds could accidentally remember another staff channel (for example
    # staff-chat). Never place the managed guide there.
    wanted = {"mod-commands", "staff-commands", "moderator-commands", "mods-commands"}
    raw_channel_id = db.get_setting(STAFF_GUIDE_CHANNEL_KEY)
    if raw_channel_id:
        try:
            existing = guild.get_channel(int(raw_channel_id))
        except (TypeError, ValueError):
            existing = None
        if isinstance(existing, discord.TextChannel) and _alias(existing.name) in wanted:
            return existing

    for channel in guild.text_channels:
        if _alias(channel.name) in wanted:
            db.set_setting(STAFF_GUIDE_CHANNEL_KEY, channel.id)
            return channel

    staff_category = None
    category_aliases = {"moderator", "moderators", "mod", "mods", "staff", "team", "staff-area"}
    for category in guild.categories:
        if _alias(category.name) in category_aliases:
            staff_category = category
            break

    if staff_category is None:
        staff_category = await guild.create_category(
            "🛠️ STAFF",
            overwrites=_staff_role_overwrites(guild),
            reason="GamerHQ managed staff area",
        )

    # A channel inside an existing private staff category inherits its permissions.
    # For a category created above, the private staff overwrites are already present.
    channel = await guild.create_text_channel(
        "🛠️・mod-commands",
        category=staff_category,
        reason="GamerHQ automatic staff command reference",
    )
    db.set_setting(STAFF_GUIDE_CHANNEL_KEY, channel.id)
    return channel


async def refresh_staff_command_guide(bot, guild: discord.Guild, channel: discord.TextChannel | None = None):
    if channel is None:
        channel = await ensure_staff_guide_channel(guild)
    else:
        db.set_setting(STAFF_GUIDE_CHANNEL_KEY, channel.id)

    content = build_staff_command_guide(bot, guild)
    if len(content) > 2000:
        # Keep the first implementation deliberately safe. If the staff command
        # surface eventually exceeds one Discord message, fail loudly instead of
        # silently dropping documentation.
        raise ServerMessageError(
            f"Staff command guide is {len(content)} characters long; Discord allows 2000. "
            "Split the guide into managed pages before adding more commands."
        )

    return await upsert_fixed_message(
        channel,
        setting_key=STAFF_GUIDE_MESSAGE_KEY,
        content=content,
        pin=True,
    )

COMMUNITY_GUIDE_CHANNEL_KEY = "server_community_commands_channel_id"
COMMUNITY_GUIDE_MESSAGE_KEYS = (
    "server_community_commands_message_1_id",
    "server_community_commands_message_2_id",
)
# v24 used a third managed page for /streamer commands. v26 removes that
# page from Community and cleans up the old managed message once.
LEGACY_COMMUNITY_STREAMER_MESSAGE_KEY = "server_community_commands_message_3_id"

STREAMER_GUIDE_CHANNEL_KEY = "server_streamer_commands_channel_id"
STREAMER_GUIDE_MESSAGE_KEY = "server_streamer_commands_message_id"


def _build_member_group_page(bot, guild: discord.Guild, root_name: str, title: str, note: str) -> str | None:
    group = _find_group(bot, guild, root_name)
    if group is None or not isinstance(group, app_commands.Group):
        return None

    lines = [f"# {title}", "", note, ""]
    for command in sorted(group.commands, key=lambda c: c.name):
        if not isinstance(command, app_commands.Command):
            continue
        usage = f"/{root_name} {command.name}{_parameter_hint(command)}"
        lines.append(f"### `{usage}`")
        lines.append(_clean_description(command.description))
        if root_name == "lfg" and command.name == "create":
            lines.append("Create a public or private gaming event. The event setup lets you choose the game, date/time, player limit and reminder.")
        elif root_name == "lfg" and command.name == "manage":
            lines.append("Open your personal event management panel for your active events.")
        elif root_name == "lfg" and command.name == "join-code":
            lines.append("Beta fallback for joining a private event invitation shared by its host.")
        elif root_name == "game" and command.name == "select":
            lines.append("You can also manage your games with the buttons in Choose Your Games.")
        lines.append("")

    text = "\n".join(lines).strip()
    if len(text) > 2000:
        raise ServerMessageError(
            f"/{root_name} member command guide is {len(text)} characters long; Discord allows 2000. "
            "Split this guide before adding more commands."
        )
    return text


def build_community_command_guide_pages(bot, guild: discord.Guild) -> list[str]:
    """General member commands only. Streamer commands intentionally live in the Streamers area."""
    pages: list[str] = []

    game = _build_member_group_page(
        bot,
        guild,
        "game",
        "🎮 GAME COMMANDS",
        "Manage the games connected to your GamerHQ profile.",
    )
    if game:
        pages.append(game)

    lfg = _build_member_group_page(
        bot,
        guild,
        "lfg",
        "🎯 LFG & EVENT COMMANDS",
        "Create, join and manage gaming sessions with other members.",
    )
    if lfg:
        pages.append(lfg)

    if not pages:
        pages.append(
            "# 🤖 GamerHQ Commands\n\n"
            "No member command groups were found in the current command tree. "
            "Restart the bot after command sync to refresh this guide."
        )
    return pages[:2]


async def ensure_community_guide_channel(guild: discord.Guild) -> discord.TextChannel:
    wanted = {"community-commands", "community-guide", "bot-commands", "gamerhq-guide"}
    raw_channel_id = db.get_setting(COMMUNITY_GUIDE_CHANNEL_KEY)
    if raw_channel_id:
        try:
            existing = guild.get_channel(int(raw_channel_id))
        except (TypeError, ValueError):
            existing = None
        if isinstance(existing, discord.TextChannel) and _alias(existing.name) in wanted:
            return existing

    for channel in guild.text_channels:
        if _alias(channel.name) in wanted:
            db.set_setting(COMMUNITY_GUIDE_CHANNEL_KEY, channel.id)
            return channel

    community = next((c for c in guild.categories if _alias(c.name) in {"community", "gamerhq-community"}), None)
    if community is None:
        community = await guild.create_category("💬 COMMUNITY", reason="GamerHQ community guide")
    channel = await guild.create_text_channel(
        "📘・community-commands",
        category=community,
        reason="GamerHQ member command guide",
    )
    db.set_setting(COMMUNITY_GUIDE_CHANNEL_KEY, channel.id)
    return channel


async def _delete_managed_message_by_setting(guild: discord.Guild, setting_key: str) -> bool:
    """Delete only the bot-managed message referenced by a stored setting.

    This is used for one-time guide migrations so we never delete arbitrary user
    messages or whole channels.
    """
    raw_message_id = db.get_setting(setting_key)
    if not raw_message_id:
        return False
    try:
        message_id = int(raw_message_id)
    except (TypeError, ValueError):
        db.set_setting(setting_key, "")
        return False

    for text_channel in guild.text_channels:
        try:
            message = await text_channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            continue
        if guild.me is not None and message.author.id != guild.me.id:
            # Never remove a message that is not ours, even if a stale setting
            # somehow points at it.
            db.set_setting(setting_key, "")
            return False
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False
        db.set_setting(setting_key, "")
        return True

    db.set_setting(setting_key, "")
    return False


async def refresh_community_command_guide(bot, guild: discord.Guild, channel: discord.TextChannel | None = None):
    if channel is None:
        channel = await ensure_community_guide_channel(guild)
    else:
        db.set_setting(COMMUNITY_GUIDE_CHANNEL_KEY, channel.id)

    # Remove the old v24 /streamer page from community-commands. Streamer
    # commands now live exclusively in the dedicated streamer-commands channel.
    await _delete_managed_message_by_setting(guild, LEGACY_COMMUNITY_STREAMER_MESSAGE_KEY)

    pages = build_community_command_guide_pages(bot, guild)
    messages = []
    for index, key in enumerate(COMMUNITY_GUIDE_MESSAGE_KEYS):
        if index >= len(pages):
            break
        messages.append(await upsert_fixed_message(channel, setting_key=key, content=pages[index], pin=True))
    return messages


def build_streamer_command_guide(bot, guild: discord.Guild) -> str:
    """Dedicated member-facing /streamer reference for the Streamers category."""
    text = _build_member_group_page(
        bot,
        guild,
        "streamer",
        "🎥 STREAMER COMMANDS",
        "Commands for your GamerHQ Streamer profile, followers and optional Streamer area.",
    )
    if text:
        return text
    return (
        "# 🎥 STREAMER COMMANDS\n\n"
        "No `/streamer` commands were found in the current command tree. "
        "Restart the bot after command sync to refresh this guide."
    )


async def ensure_streamer_guide_channel(guild: discord.Guild) -> discord.TextChannel:
    # streamer-guide is a separate onboarding/help channel. Never adopt it as the
    # command reference. The command guide must have its own dedicated channel.
    wanted = {"streamer-commands"}
    raw_channel_id = db.get_setting(STREAMER_GUIDE_CHANNEL_KEY)
    if raw_channel_id:
        try:
            existing = guild.get_channel(int(raw_channel_id))
        except (TypeError, ValueError):
            existing = None
        if isinstance(existing, discord.TextChannel) and _alias(existing.name) in wanted:
            return existing

        # v25 could store streamer-guide here. Remove only the managed command
        # message from that old channel; preserve the channel and its real guide.
        await _delete_managed_message_by_setting(guild, STREAMER_GUIDE_MESSAGE_KEY)
        db.set_setting(STREAMER_GUIDE_CHANNEL_KEY, "")

    # Prefer the actual GamerHQ Streamers category created by the streamer system.
    streamer_category = next(
        (c for c in guild.categories if _alias(c.name) in {"streamers", "gamerhq-streamers"}),
        None,
    )
    if streamer_category is None:
        streamer_category = await guild.create_category(
            "🎥 STREAMERS",
            reason="GamerHQ streamer command guide",
        )

    # Only an actual streamer-commands channel may be reused.
    for channel in streamer_category.text_channels:
        if _alias(channel.name) in wanted:
            db.set_setting(STREAMER_GUIDE_CHANNEL_KEY, channel.id)
            return channel

    channel = await guild.create_text_channel(
        "📘・streamer-commands",
        category=streamer_category,
        reason="GamerHQ automatic streamer command reference",
    )
    db.set_setting(STREAMER_GUIDE_CHANNEL_KEY, channel.id)
    return channel


async def refresh_streamer_command_guide(bot, guild: discord.Guild, channel: discord.TextChannel | None = None):
    if channel is None:
        channel = await ensure_streamer_guide_channel(guild)
    else:
        db.set_setting(STREAMER_GUIDE_CHANNEL_KEY, channel.id)

    content = build_streamer_command_guide(bot, guild)
    return await upsert_fixed_message(
        channel,
        setting_key=STREAMER_GUIDE_MESSAGE_KEY,
        content=content,
        pin=True,
    )
