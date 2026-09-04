from __future__ import annotations

from datetime import datetime, timedelta
import re
import secrets
import traceback
import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import db
from services.lfg_service import (
    SERVER_TZ, find_lfg_channel, find_game_lfg_channel, member_has_game_role,
    render_event, user_games,
)


async def refresh_event_posts(guild: discord.Guild, event_id: int):
    event = db.get_lfg_event(event_id)
    if not event:
        return
    content = render_event(guild, event)
    view = LFGEventView(event_id)
    rows = db.get_lfg_event_messages(event_id)
    # Compatibility with events created before multi-post support.
    if not rows and event.get("channel_id") and event.get("message_id"):
        rows = [{"channel_id": event["channel_id"], "message_id": event["message_id"]}]
    for row in rows:
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            await msg.edit(content=content, view=view)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


async def delete_event_posts(guild: discord.Guild, event: dict) -> None:
    rows = db.get_lfg_event_messages(int(event["id"]))
    if not rows and event.get("channel_id") and event.get("message_id"):
        rows = [{"channel_id": event["channel_id"], "message_id": event["message_id"]}]
    for row in rows:
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            message = await channel.fetch_message(int(row["message_id"]))
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


async def notify_cancelled_users(guild: discord.Guild, event: dict) -> None:
    game = db.get_game_by_id(int(event["game_id"]))
    game_name = game["name"] if game else "Gaming event"
    recipients = {
        int(row["user_id"])
        for row in db.get_lfg_event_members(int(event["id"]))
        if row["status"] in {"joined", "invited"} and int(row["user_id"]) != int(event["host_id"])
    }
    for user_id in recipients:
        member = guild.get_member(user_id)
        if member is None or member.bot:
            continue
        try:
            await member.send(
                f"# ❌ GamerHQ Event Cancelled\n\n"
                f"**{event['title']}** for **{game_name}** was cancelled by the host."
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


async def delete_event_voice(guild: discord.Guild, event: dict) -> None:
    channel_id = event.get("voice_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if isinstance(channel, discord.VoiceChannel):
        try:
            await channel.delete(reason=f"GamerHQ LFG event #{event['id']} cancelled/finished")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    db.clear_lfg_event_voice(int(event["id"]))


def event_voice_overwrites(guild: discord.Guild, event: dict) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
    }
    bot_member = guild.me
    if bot_member:
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, manage_channels=True, move_members=True
        )
    joined_ids = {
        int(row["user_id"]) for row in db.get_lfg_event_members(int(event["id"]))
        if row["status"] == "joined"
    }
    for user_id in joined_ids:
        member = guild.get_member(user_id)
        if member is None:
            continue
        is_host = user_id == int(event["host_id"])
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True,
            manage_channels=is_host, move_members=is_host,
        )
    return overwrites


async def send_voice_ready_dm(guild: discord.Guild, event: dict, member: discord.Member, channel: discord.VoiceChannel) -> bool:
    """Send the Voice Ready DM at most once for this event/member."""
    event_id = int(event["id"])
    fresh = db.get_lfg_event(event_id)
    if not fresh or fresh.get("status") != "scheduled":
        return False

    # Reserve before sending so scheduler restarts/retries cannot duplicate the DM.
    now_ts = int(datetime.now(SERVER_TZ).timestamp())
    if not db.claim_lfg_voice_notification(event_id, member.id, now_ts):
        return False

    # Cancellation may have happened between the reservation and the send.
    fresh = db.get_lfg_event(event_id)
    if not fresh or fresh.get("status") != "scheduled":
        return False

    game = db.get_game_by_id(int(fresh["game_id"]))
    game_name = game["name"] if game else "Gaming event"
    host = guild.get_member(int(fresh["host_id"]))
    host_label = host.mention if host else f"<@{int(fresh['host_id'])}>"
    lead = int(fresh["invite_lead_minutes"])
    try:
        await member.send(
            f"# 🎧 Your GamerHQ Voice is Ready\n\n"
            f"**{fresh['title']}** · **{game_name}**\n"
            f"👤 Hosted by {host_label}\n"
            f"📅 <t:{int(fresh['start_at'])}:F> (<t:{int(fresh['start_at'])}:R>)\n"
            f"🔔 Starts in about **{lead} minutes**\n\n"
            f"Your private event voice channel is ready:\n{channel.jump_url}"
        )
        return True
    except (discord.Forbidden, discord.HTTPException):
        # Deliberately do not retry automatically: avoiding duplicate/spam DMs is more important.
        return False


async def create_event_voice(guild: discord.Guild, event: dict) -> discord.VoiceChannel | None:
    event_id = int(event["id"])
    fresh = db.get_lfg_event(event_id)
    if not fresh or fresh.get("status") != "scheduled":
        return None

    existing_id = fresh.get("voice_channel_id")
    if existing_id:
        existing = guild.get_channel(int(existing_id))
        if isinstance(existing, discord.VoiceChannel):
            return existing

    game = db.get_game_by_id(int(fresh["game_id"]))
    category = guild.get_channel(int(game["category_id"])) if game and game.get("category_id") else None
    try:
        channel = await guild.create_voice_channel(
            name=f"🎮・{fresh['title']}"[:100],
            category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=event_voice_overwrites(guild, fresh),
            reason=f"GamerHQ LFG event #{event_id} voice",
        )
    except (discord.Forbidden, discord.HTTPException):
        return None

    # Only one scheduler/process may claim the event voice, and never after cancellation.
    if not db.claim_lfg_event_voice(event_id, channel.id):
        try:
            await channel.delete(reason=f"GamerHQ LFG event #{event_id} duplicate/cancelled voice")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        current = db.get_lfg_event(event_id)
        if current and current.get("voice_channel_id"):
            existing = guild.get_channel(int(current["voice_channel_id"]))
            if isinstance(existing, discord.VoiceChannel):
                return existing
        return None

    fresh = db.get_lfg_event(event_id)
    if not fresh or fresh.get("status") != "scheduled":
        try:
            await channel.delete(reason=f"GamerHQ LFG event #{event_id} cancelled during voice creation")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        return None

    for row in db.get_lfg_event_members(event_id):
        if row["status"] != "joined":
            continue
        # Re-check before every external DM.
        current = db.get_lfg_event(event_id)
        if not current or current.get("status") != "scheduled":
            break
        member = guild.get_member(int(row["user_id"]))
        if member and not member.bot:
            await send_voice_ready_dm(guild, current, member, channel)
    await refresh_event_posts(guild, event_id)
    return channel


class ConfirmCancelEventView(discord.ui.View):
    def __init__(self, event_id: int, requester_id: int):
        super().__init__(timeout=120)
        self.event_id = int(event_id)
        self.requester_id = int(requester_id)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This confirmation belongs to another user.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Delete Event", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
        event = db.get_lfg_event(self.event_id)
        if not event:
            return await interaction.response.edit_message(content="❌ This event no longer exists.", view=None)

        is_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
        if interaction.user.id != int(event["host_id"]) and not is_admin:
            return await interaction.response.send_message("❌ Only the host or an administrator can delete this event.", ephemeral=True)

        # Stop the scheduler first. A cancelled event must never create/send a voice afterwards.
        db.set_lfg_event_status(self.event_id, "cancelled")
        event = db.get_lfg_event(self.event_id) or event
        await notify_cancelled_users(interaction.guild, event)
        await delete_event_posts(interaction.guild, event)
        await delete_event_voice(interaction.guild, event)
        await delete_private_event_channel(interaction.guild, event)
        db.delete_lfg_event(self.event_id)
        await interaction.response.edit_message(
            content="✅ Event deleted. Participants were notified where possible.",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Deletion cancelled. Nothing was changed.", view=None)
        self.stop()

def _safe_event_channel_name(title: str, event_id: int) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or f"event-{event_id}"
    return f"🔒・{clean}"[:100]


def _event_private_channel(guild: discord.Guild, event: dict) -> discord.TextChannel | None:
    channel_id = event.get("private_channel_id")
    if not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None


async def _grant_private_event_access(guild: discord.Guild, event: dict, member: discord.Member) -> None:
    channel = _event_private_channel(guild, event)
    if channel is not None:
        try:
            await channel.set_permissions(member, view_channel=True, read_message_history=True, send_messages=True)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def _revoke_private_event_access(guild: discord.Guild, event: dict, member: discord.Member) -> None:
    channel = _event_private_channel(guild, event)
    if channel is not None and member.id != int(event["host_id"]):
        try:
            await channel.set_permissions(member, overwrite=None)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def create_private_event_channel(guild: discord.Guild, event: dict, game: dict) -> discord.TextChannel | None:
    category = guild.get_channel(int(game["category_id"])) if game.get("category_id") else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True,
            manage_channels=True, manage_messages=True,
        )
    host = guild.get_member(int(event["host_id"]))
    if host:
        overwrites[host] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True, manage_messages=True,
        )
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        if role.permissions.administrator or role.permissions.manage_guild or role.permissions.moderate_members:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)
    for row in db.get_lfg_event_members(int(event["id"])):
        if row["status"] not in {"joined", "invited"}:
            continue
        member = guild.get_member(int(row["user_id"]))
        if member and not member.bot:
            overwrites[member] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)
    try:
        channel = await guild.create_text_channel(
            _safe_event_channel_name(str(event["title"]), int(event["id"])),
            category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=overwrites,
            topic=f"Private GamerHQ event #{int(event['id'])} · {event['title']}",
            reason=f"GamerHQ private LFG event #{int(event['id'])}",
        )
    except (discord.Forbidden, discord.HTTPException):
        return None
    db.set_lfg_event_private_channel(int(event["id"]), channel.id)
    return channel


async def delete_private_event_channel(guild: discord.Guild, event: dict) -> None:
    channel = _event_private_channel(guild, event)
    if channel is not None:
        try:
            await channel.delete(reason=f"GamerHQ private event #{int(event['id'])} cancelled/finished")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


async def make_server_invite(channel: discord.TextChannel) -> str | None:
    try:
        invite = await channel.create_invite(max_age=86400, max_uses=0, unique=False, reason="GamerHQ event share")
        return invite.url
    except (discord.Forbidden, discord.HTTPException):
        return None


async def _finish_join(guild: discord.Guild, event: dict, member: discord.Member) -> str:
    members = db.get_lfg_event_members(int(event["id"]))
    states = {int(r["user_id"]): r["status"] for r in members}
    if states.get(member.id) == "joined":
        return "already"
    if sum(r["status"] == "joined" for r in members) >= int(event["max_players"]):
        return "full"
    db.set_lfg_event_member(int(event["id"]), member.id, "joined")
    fresh = db.get_lfg_event(int(event["id"]))
    if fresh and fresh.get("visibility") == "private":
        await _grant_private_event_access(guild, fresh, member)
    if fresh and fresh.get("voice_channel_id"):
        voice = guild.get_channel(int(fresh["voice_channel_id"]))
        if isinstance(voice, discord.VoiceChannel):
            try:
                await voice.set_permissions(member, view_channel=True, connect=True, speak=True)
            except (discord.Forbidden, discord.HTTPException):
                pass
            await send_voice_ready_dm(guild, fresh, member, voice)
    await refresh_event_posts(guild, int(event["id"]))
    return "joined"


class AddGameAndJoinView(discord.ui.View):
    def __init__(self, event_id: int, guild_id: int, user_id: int):
        super().__init__(timeout=120)
        self.event_id = int(event_id)
        self.guild_id = int(guild_id)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This confirmation belongs to another user.", ephemeral=interaction.guild is not None)
            return False
        return True

    @discord.ui.button(label="Add Game & Join", emoji="🎮", style=discord.ButtonStyle.success)
    async def add_and_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.client.get_guild(self.guild_id)
        event = db.get_lfg_event(self.event_id)
        member = guild.get_member(self.user_id) if guild else None
        if guild is None or member is None or not event or event.get("status") != "scheduled":
            return await interaction.response.edit_message(content="❌ This event is no longer available.", view=None)
        game = db.get_game_by_id(int(event["game_id"]))
        role = guild.get_role(int(game["role_id"])) if game and game.get("role_id") else None
        if not game or role is None:
            return await interaction.response.edit_message(content="❌ The game role is currently unavailable.", view=None)
        try:
            if role not in member.roles:
                await member.add_roles(role, reason="GamerHQ Add Game & Join")
        except discord.Forbidden:
            return await interaction.response.edit_message(content="❌ I couldn't add the game role. Please ask staff to check my role permissions.", view=None)
        except discord.HTTPException as exc:
            return await interaction.response.edit_message(content=f"❌ Discord couldn't add the game role: `{exc}`", view=None)

        result = await _finish_join(guild, event, member)
        # Adding a member role does not change selector content. Refreshing here
        # previously passed no views and stripped every public game button.
        if result == "full":
            msg = f"🎮 **{game['name']}** was added to your games, but this event is already full."
        elif result == "already":
            msg = f"✅ **{game['name']}** was added to your games. You're already in this event."
        else:
            msg = f"✅ **{game['name']}** was added to your games and you've joined the event."
        await interaction.response.edit_message(content=msg, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", view=None)
        self.stop()


async def prompt_add_game_and_join(interaction: discord.Interaction, event: dict, game: dict, guild: discord.Guild):
    content = (
        f"# 🎮 Add {game['name']}?\n\n"
        f"**{game['name']}** isn't in your GamerHQ games yet.\n"
        "Add the game to your GamerHQ games and join this event?"
    )
    await interaction.response.send_message(
        content,
        view=AddGameAndJoinView(int(event["id"]), guild.id, interaction.user.id),
        ephemeral=interaction.guild is not None,
    )


class LFGEventView(discord.ui.View):
    def __init__(self, event_id: int):
        super().__init__(timeout=None)
        self.event_id = int(event_id)
        join = discord.ui.Button(label="Join Event", emoji="✅", style=discord.ButtonStyle.success,
                                 custom_id=f"gamerhq:lfg:join:{self.event_id}")
        leave = discord.ui.Button(label="Leave", emoji="↩️", style=discord.ButtonStyle.secondary,
                                  custom_id=f"gamerhq:lfg:leave:{self.event_id}")
        # Public event posts only contain actions that are meant for everyone.
        # Host-only management lives in the ephemeral /lfg manage panel, so other
        # members never see a destructive Cancel Event button they cannot use.
        event = db.get_lfg_event(self.event_id)
        share_label = "Share Invite" if event and event.get("visibility") == "private" else "Share Event"
        share = discord.ui.Button(label=share_label, emoji="🔗", style=discord.ButtonStyle.secondary,
                                  custom_id=f"gamerhq:lfg:share:{self.event_id}")
        join.callback, leave.callback, share.callback = self.join_event, self.leave_event, self.share_event
        self.add_item(join); self.add_item(leave); self.add_item(share)

    async def join_event(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            return await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
        event = db.get_lfg_event(self.event_id)
        if not event or event["status"] != "scheduled":
            return await interaction.response.send_message("❌ This event is no longer available.", ephemeral=True)
        game = db.get_game_by_id(int(event["game_id"]))
        if not game:
            return await interaction.response.send_message("❌ This event's game is unavailable.", ephemeral=True)
        if event.get("visibility") == "private":
            states = {int(r["user_id"]): r["status"] for r in db.get_lfg_event_members(self.event_id)}
            if interaction.user.id != int(event["host_id"]) and states.get(interaction.user.id) not in {"invited", "joined"}:
                return await interaction.response.send_message(
                    "🔒 This is a private event. Use its private invite code or ask the host for an invitation.", ephemeral=True
                )
        if not member_has_game_role(interaction.user, game):
            return await prompt_add_game_and_join(interaction, event, game, interaction.guild)
        result = await _finish_join(interaction.guild, event, interaction.user)
        if result == "already":
            return await interaction.response.send_message("✅ You're already in this event.", ephemeral=True)
        if result == "full":
            return await interaction.response.send_message("❌ This event is full.", ephemeral=True)
        await interaction.response.send_message("✅ You've joined the event.", ephemeral=True)

    async def share_event(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
        event = db.get_lfg_event(self.event_id)
        if not event or event.get("status") != "scheduled":
            return await interaction.response.send_message("❌ This event is no longer available.", ephemeral=True)
        if event.get("visibility") == "private":
            if interaction.user.id != int(event["host_id"]):
                return await interaction.response.send_message("🔒 Only the host can share this private event invite.", ephemeral=True)
            token = event.get("share_token")
            if not token or not int(event.get("share_enabled", 1)):
                return await interaction.response.send_message("❌ The private invite link is currently disabled.", ephemeral=True)
            private_channel = _event_private_channel(interaction.guild, event)
            invite_url = await make_server_invite(private_channel) if private_channel else None
            server_line = f"**Server invite:** {invite_url}\n" if invite_url else ""
            return await interaction.response.send_message(
                "# 🔒 Share Private Event\n\n"
                f"{server_line}"
                f"**Event access:** `{token}`\n\n"
                "**Already on GamerHQ?** Use `/lfg join-code` with the Event access value above.\n"
                "**New to GamerHQ?** Open the Server invite first, then use the same Event access value after joining.\n\n"
                "This is the Discord-only Beta flow. The event remains private and capacity checks still apply.",
                ephemeral=True,
            )
        rows = db.get_lfg_event_messages(self.event_id)
        url = None
        for row in rows:
            channel = interaction.guild.get_channel(int(row["channel_id"]))
            if isinstance(channel, discord.TextChannel):
                try:
                    msg = await channel.fetch_message(int(row["message_id"]))
                    url = msg.jump_url
                    break
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
        if not url:
            return await interaction.response.send_message("❌ Event link is currently unavailable.", ephemeral=True)
        await interaction.response.send_message(f"# 🔗 Share Event\n\n{url}", ephemeral=True)

    async def leave_event(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
        event = db.get_lfg_event(self.event_id)
        if not event:
            return await interaction.response.send_message("❌ This event is no longer available.", ephemeral=True)
        if interaction.user.id == int(event["host_id"]):
            return await interaction.response.send_message("❌ The host can't leave their own event yet.", ephemeral=True)
        members = db.get_lfg_event_members(self.event_id)
        status = next((r["status"] for r in members if int(r["user_id"]) == interaction.user.id), None)
        if status != "joined":
            return await interaction.response.send_message("You're not currently joined.", ephemeral=True)
        db.remove_lfg_event_member(self.event_id, interaction.user.id)
        if event.get("visibility") == "private" and isinstance(interaction.user, discord.Member):
            await _revoke_private_event_access(interaction.guild, event, interaction.user)
        await interaction.response.send_message("↩️ You've left the event.", ephemeral=True)
        await refresh_event_posts(interaction.guild, self.event_id)



class HostedEventSelect(discord.ui.Select):
    def __init__(self, parent: "LFGManageView", events: list[dict]):
        self.parent_view = parent
        options = []
        for event in events[:25]:
            game = db.get_game_by_id(int(event["game_id"]))
            game_name = game["name"] if game else "Unknown game"
            start_at = int(event["start_at"])
            options.append(
                discord.SelectOption(
                    label=str(event["title"])[:100],
                    description=f"{game_name} • {datetime.fromtimestamp(start_at, SERVER_TZ).strftime('%b %d, %H:%M')}"[:100],
                    value=str(event["id"]),
                    emoji="🎮",
                )
            )
        super().__init__(
            placeholder="Choose one of your active events...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_event_id = int(self.values[0])
        self.parent_view._sync_buttons()
        event = db.get_lfg_event(self.parent_view.selected_event_id)
        if not event or int(event["host_id"]) != self.parent_view.host_id or event.get("status") != "scheduled":
            return await interaction.response.edit_message(
                content="❌ That event is no longer available.", view=self.parent_view
            )
        game = db.get_game_by_id(int(event["game_id"]))
        game_name = game["name"] if game else "Unknown game"
        await interaction.response.edit_message(
            content=(
                "# ⚙️ Manage Your LFG Events\n\n"
                f"**Selected:** {event['title']}\n"
                f"🎮 **Game:** {game_name}\n"
                f"📅 **Starts:** <t:{int(event['start_at'])}:F>\n\n"
                "Only you can see and use these management controls."
            ),
            view=self.parent_view,
        )


class LFGManageView(discord.ui.View):
    def __init__(self, host_id: int, events: list[dict]):
        super().__init__(timeout=300)
        self.host_id = int(host_id)
        self.selected_event_id: int | None = None
        self.add_item(HostedEventSelect(self, events))
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("This event manager belongs to another user.", ephemeral=True)
            return False
        return True

    def _sync_buttons(self):
        disabled = self.selected_event_id is None
        self.cancel_selected.disabled = disabled
        self.share_selected.disabled = disabled
        self.rotate_private_link.disabled = disabled

    @discord.ui.button(label="Share", emoji="🔗", style=discord.ButtonStyle.primary, row=1, disabled=True)
    async def share_selected(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_event_id:
            return await interaction.response.send_message("Choose an event first.", ephemeral=True)
        event = db.get_lfg_event(self.selected_event_id)
        if not event or int(event["host_id"]) != self.host_id:
            return await interaction.response.send_message("❌ That event is no longer available.", ephemeral=True)
        if event.get("visibility") == "private":
            token = event.get("share_token")
            if not token or not int(event.get("share_enabled", 1)):
                return await interaction.response.send_message("❌ Private sharing is disabled for this event.", ephemeral=True)
            channel = _event_private_channel(interaction.guild, event) if interaction.guild else None
            invite_url = await make_server_invite(channel) if channel else None
            server_line = f"**Server invite:** {invite_url}\n" if invite_url else ""
            return await interaction.response.send_message(
                f"# 🔒 Share Private Event\n\n{server_line}**Event access:** `{token}`\n\n"
                "Already a member? Use `/lfg join-code` with the Event access value. "
                "New member? Join GamerHQ first, then use the same value.", ephemeral=True
            )
        rows = db.get_lfg_event_messages(self.selected_event_id)
        for row in rows:
            channel = interaction.guild.get_channel(int(row["channel_id"])) if interaction.guild else None
            if isinstance(channel, discord.TextChannel):
                try:
                    msg = await channel.fetch_message(int(row["message_id"]))
                    return await interaction.response.send_message(f"# 🔗 Share Event\n\n{msg.jump_url}", ephemeral=True)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
        await interaction.response.send_message("❌ Event link is currently unavailable.", ephemeral=True)

    @discord.ui.button(label="Regenerate Invite", emoji="🔄", style=discord.ButtonStyle.secondary, row=1, disabled=True)
    async def rotate_private_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_event_id:
            return await interaction.response.send_message("Choose an event first.", ephemeral=True)
        event = db.get_lfg_event(self.selected_event_id)
        if not event or int(event["host_id"]) != self.host_id:
            return await interaction.response.send_message("❌ That event is no longer available.", ephemeral=True)
        if event.get("visibility") != "private":
            return await interaction.response.send_message("🌐 Public events use their normal Share Event link and do not need a private invite.", ephemeral=True)
        token = secrets.token_urlsafe(8)
        db.rotate_lfg_share_token(self.selected_event_id, token)
        await interaction.response.send_message(
            f"✅ The old private invite is disabled. New Event access: `{token}`\nShare it only with people you want to let into this event.",
            ephemeral=True,
        )

    @discord.ui.button(label="Cancel Event", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, disabled=True)
    async def cancel_selected(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_event_id:
            return await interaction.response.send_message("Choose an event first.", ephemeral=True)
        event = db.get_lfg_event(self.selected_event_id)
        if not event or event.get("status") != "scheduled":
            return await interaction.response.edit_message(content="❌ This event is no longer available.", view=None)
        if int(event["host_id"]) != self.host_id:
            return await interaction.response.edit_message(content="❌ You can only manage events you created.", view=None)
        joined = [r for r in db.get_lfg_event_members(self.selected_event_id) if r["status"] == "joined"]
        affected = max(0, len(joined) - 1)
        await interaction.response.edit_message(
            content=(
                "# 🗑️ Cancel Event?\n\n"
                f"**{event['title']}** will be removed from every GamerHQ LFG channel.\n"
                f"👥 **{affected} other joined player{'s' if affected != 1 else ''}** will be affected.\n\n"
                "This cannot be undone."
            ),
            view=ConfirmCancelEventView(self.selected_event_id, self.host_id),
        )
        self.stop()

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ LFG event manager closed.", view=None)
        self.stop()


class LFGInviteDMView(discord.ui.View):
    def __init__(self, event_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.event_id = int(event_id)
        self.guild_id = int(guild_id)
        button = discord.ui.Button(
            label="Join Event", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"gamerhq:lfg:dmjoin:{self.event_id}",
        )
        button.callback = self.join_from_dm
        self.add_item(button)

    async def join_from_dm(self, interaction: discord.Interaction):
        guild = interaction.client.get_guild(self.guild_id)
        if guild is None:
            return await interaction.response.send_message("❌ GamerHQ server is currently unavailable.")
        member = guild.get_member(interaction.user.id)
        event = db.get_lfg_event(self.event_id)
        if member is None or event is None or event.get("status") != "scheduled":
            return await interaction.response.send_message("❌ This event is no longer available.")
        game = db.get_game_by_id(int(event["game_id"]))
        if not game:
            return await interaction.response.send_message("❌ This event's game is unavailable.")
        if not member_has_game_role(member, game):
            return await prompt_add_game_and_join(interaction, event, game, guild)
        result = await _finish_join(guild, event, member)
        if result == "already":
            return await interaction.response.send_message("✅ You're already in this event.")
        if result == "full":
            return await interaction.response.send_message("❌ This event is full.")
        await interaction.response.send_message("✅ You've joined the event. See you there!")


async def send_event_invites(guild: discord.Guild, event: dict, event_message: discord.Message) -> None:
    game = db.get_game_by_id(int(event["game_id"]))
    game_name = game["name"] if game else "Gaming event"
    host = guild.get_member(int(event["host_id"]))
    host_label = host.mention if host is not None else f"<@{int(event['host_id'])}>"
    invited = [
        int(row["user_id"]) for row in db.get_lfg_event_members(int(event["id"]))
        if row["status"] == "invited"
    ]
    for user_id in invited:
        member = guild.get_member(user_id)
        if member is None or member.bot:
            continue
        try:
            await member.send(
                f"# 🎮 GamerHQ Event Invite\n\n"
                f"You've been invited to **{event['title']}** for **{game_name}**.\n"
                f"👤 **Invited by:** {host_label}\n"
                f"📅 <t:{int(event['start_at'])}:F> (<t:{int(event['start_at'])}:R>)\n\n"
                f"Click **Join Event** below to join immediately.\n"
                f"Event post: {event_message.jump_url}",
                view=LFGInviteDMView(int(event["id"]), guild.id),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


class PreviewInviteUsersSelect(discord.ui.UserSelect):
    def __init__(self, preview):
        self.preview = preview
        count = len(preview.builder.invited_ids)
        placeholder = f"👤 Invite players · {count} selected" if count else "👤 Invite players (optional)"
        super().__init__(placeholder=placeholder[:150], min_values=0, max_values=10, row=0)

    async def callback(self, interaction: discord.Interaction):
        b = self.preview.builder
        b.invited_ids = {member.id for member in self.values if member.id != b.host.id and not member.bot}
        self.preview.rebuild()
        await interaction.response.edit_message(content=self.preview.content(), view=self.preview)


class EventDraftView(discord.ui.View):
    """Final preview. Optional invites are selected here; all other settings come from the builder."""
    def __init__(self, builder):
        super().__init__(timeout=600)
        self.builder = builder
        self.host = builder.host
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        self.add_item(PreviewInviteUsersSelect(self))
        for item in (self.edit_event, self.create_event, self.cancel):
            self.add_item(item)

    def content(self):
        b = self.builder
        hour, minute = b.hour, b.minute
        dt = datetime.fromisoformat(b.date).replace(hour=hour, minute=minute, tzinfo=SERVER_TZ)
        start_at = int(dt.timestamp())
        inv = " · ".join(f"<@{x}>" for x in sorted(b.invited_ids)) or "None"
        return (
            "# 🎮 Event Preview\n\n"
            f"**Title:** {b.title}\n"
            f"**Game:** {b.game['emoji']} {b.game['name']}\n"
            f"**Start:** <t:{start_at}:F> (<t:{start_at}:R>)\n"
            f"**Players:** {b.max_players} total\n"
            f"**Voice invite:** {b.invite_lead} minutes before\n"
            f"**Visibility:** {'🔒 Private · Invite only' if b.visibility == 'private' else '🌐 Public'}\n\n"
            f"**Invited:** {inv}\n\n"
            "Optionally select players to invite above. They can join even if they have not selected the game yet; GamerHQ will offer to add the game role when they join.\n\n"
            "Nothing has been posted yet."
        )

    async def interaction_check(self, interaction):
        if interaction.user.id != self.host.id:
            await interaction.response.send_message("This draft belongs to another user.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Edit", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
    async def edit_event(self, interaction, button):
        self.builder._rebuild()
        await interaction.response.edit_message(content=self.builder.content(), view=self.builder)
        self.stop()

    @discord.ui.button(label="Create Event", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def create_event(self, interaction, button):
        # Creating channels/posts/DMs can take longer than Discord's interaction
        # acknowledgement window. Defer first, then edit the same ephemeral draft.
        await interaction.response.defer(ephemeral=True, thinking=True)
        b = self.builder
        general = find_lfg_channel(interaction.guild)
        game_channel = find_game_lfg_channel(interaction.guild, b.game)
        if not general:
            return await interaction.edit_original_response(content="❌ General looking-for-group channel not found.", view=None)

        hour, minute = b.hour, b.minute
        dt = datetime.fromisoformat(b.date).replace(hour=hour, minute=minute, tzinfo=SERVER_TZ)
        start_at = int(dt.timestamp())
        event = db.create_lfg_event(
            guild_id=interaction.guild.id, game_id=b.game["id"], host_id=b.host.id,
            title=b.title, start_at=start_at, max_players=b.max_players,
            invite_lead_minutes=b.invite_lead, visibility=b.visibility,
            share_token=secrets.token_urlsafe(8),
        )
        for uid in b.invited_ids:
            member = interaction.guild.get_member(int(uid))
            if member and not member.bot:
                db.set_lfg_event_member(event["id"], uid, "invited")

        event = db.get_lfg_event(event["id"])
        view = LFGEventView(event["id"])
        posts = []
        target_channels = []
        if b.visibility == "private":
            private_channel = await create_private_event_channel(interaction.guild, event, b.game)
            if not private_channel:
                db.delete_lfg_event(int(event["id"]))
                return await interaction.edit_original_response(content="❌ I could not create the private event channel.", view=None)
            event = db.get_lfg_event(event["id"])
            target_channels = [private_channel]
        else:
            target_channels = list(dict.fromkeys([general, game_channel]))
        for channel in target_channels:
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                post = await channel.send(
                    render_event(interaction.guild, event), view=view,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                db.add_lfg_event_message(event["id"], channel_id=channel.id, message_id=post.id)
                posts.append(post)
            except (discord.Forbidden, discord.HTTPException):
                pass
        if not posts:
            return await interaction.edit_original_response(content="❌ I could not post the event.", view=None)
        db.set_lfg_event_message(event["id"], channel_id=posts[0].channel.id, message_id=posts[0].id)
        await send_event_invites(interaction.guild, db.get_lfg_event(event["id"]), posts[0])
        where = " and ".join(p.channel.mention for p in posts)
        visibility_note = "🔒 Private event" if b.visibility == "private" else "🌐 Public event"
        await interaction.edit_original_response(content=f"✅ {visibility_note} created in {where}.", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="✖️ Event creation cancelled.", view=None)
        self.stop()


class EventTitleModal(discord.ui.Modal, title="Event Title"):
    def __init__(self, builder):
        super().__init__(timeout=300)
        self.builder = builder
        self.title_input = discord.ui.TextInput(
            label="Event title",
            placeholder="e.g. Ranked Night",
            default=builder.title[:60] if builder.title else "",
            max_length=60,
        )
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction):
        value = str(self.title_input).strip()
        if not value:
            return await interaction.response.send_message("❌ Please enter an event title.", ephemeral=True)
        self.builder.title = value
        self.builder._rebuild()
        await interaction.response.edit_message(content=self.builder.content(), view=self.builder)


class BuilderGameSelect(discord.ui.Select):
    def __init__(self, builder):
        self.builder = builder
        options = []
        for game in builder.games[:25]:
            options.append(discord.SelectOption(
                label=game["name"][:100], value=str(game["id"]), emoji=game.get("emoji") or "🎮",
                default=builder.game is not None and int(game["id"]) == int(builder.game["id"]),
            ))
        placeholder = f"🎮 {builder.game['name']}" if builder.game else "🎮 Choose game"
        super().__init__(placeholder=placeholder[:150], options=options, row=0, disabled=builder.game_locked)

    async def callback(self, interaction):
        self.builder.game = next(g for g in self.builder.games if str(g["id"]) == self.values[0])
        # Invites belong to the selected game. Never carry them across a game change.
        self.builder.invited_ids.clear()
        self.builder._rebuild()
        await interaction.response.edit_message(content=self.builder.content(), view=self.builder)


class BuilderDateSelect(discord.ui.Select):
    def __init__(self, builder):
        self.builder = builder
        today = datetime.now(SERVER_TZ).date()
        options = []
        for i in range(14):
            d = today + timedelta(days=i)
            prefix = "Today" if i == 0 else "Tomorrow" if i == 1 else d.strftime("%A")
            options.append(discord.SelectOption(
                label=f"{prefix} · {d.strftime('%d.%m.%Y')}", value=d.isoformat(), emoji="📅",
                default=builder.date == d.isoformat(),
            ))
        label = datetime.fromisoformat(builder.date).strftime("%a, %d.%m.%Y") if builder.date else "Choose date"
        super().__init__(placeholder=f"📅 {label}"[:150], options=options, row=1)

    async def callback(self, interaction):
        self.builder.date = self.values[0]
        self.builder._rebuild()
        await interaction.response.edit_message(content=self.builder.content(), view=self.builder)


class BuilderTimeSelect(discord.ui.Select):
    def __init__(self, builder):
        self.builder = builder
        options = []
        selected = builder.time_12h
        for h12 in [12] + list(range(1, 12)):
            for minute in (0, 30):
                label = f"{h12}:{minute:02d}"
                options.append(discord.SelectOption(
                    label=label,
                    value=label,
                    emoji="🕐",
                    default=selected == label,
                ))
        placeholder = f"🕐 Time · {selected}" if selected else "🕐 Choose time"
        super().__init__(placeholder=placeholder[:150], options=options, row=2)

    async def callback(self, interaction):
        self.builder.time_12h = self.values[0]
        self.builder._sync_24h_time()
        self.builder._rebuild()
        await interaction.response.edit_message(content=self.builder.content(), view=self.builder)


class BuilderPlayersSelect(discord.ui.Select):
    def __init__(self, builder):
        self.builder = builder
        options = [discord.SelectOption(label=f"{n} players", value=str(n), emoji="👥", default=builder.max_players == n) for n in range(2, 26)]
        placeholder = f"👥 {builder.max_players} players" if builder.max_players else "👥 Choose total players"
        super().__init__(placeholder=placeholder[:150], options=options, row=3)

    async def callback(self, interaction):
        self.builder.max_players = int(self.values[0])
        self.builder._rebuild()
        await interaction.response.edit_message(content=self.builder.content(), view=self.builder)


class EventBuilderView(discord.ui.View):
    def __init__(self, *, host, games, game=None, game_locked=False):
        super().__init__(timeout=600)
        self.host = host
        self.games = games
        self.game = game
        self.game_locked = game_locked
        self.title = "Gaming Session"
        self.date = None
        self.time_12h = None
        self.period = "PM"
        self.hour = None
        self.minute = 0
        self.max_players = 5
        self.invite_lead = 15
        self.visibility = "public"
        self.invited_ids = set()
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        self.ampm_button.label = self.period
        self.voice_lead.label = f"{self.invite_lead}m"
        self.visibility_button.label = "Public" if self.visibility == "public" else "Private"
        self.visibility_button.emoji = "🌐" if self.visibility == "public" else "🔒"
        self.add_item(BuilderGameSelect(self))
        self.add_item(BuilderDateSelect(self))
        self.add_item(BuilderTimeSelect(self))
        self.add_item(BuilderPlayersSelect(self))
        for item in (self.edit_title, self.ampm_button, self.voice_lead, self.visibility_button, self.preview):
            self.add_item(item)

    def _sync_24h_time(self):
        if not self.time_12h:
            self.hour = None
            self.minute = 0
            return
        hour_text, minute_text = self.time_12h.split(":", 1)
        hour12 = int(hour_text)
        self.minute = int(minute_text)
        if self.period == "AM":
            self.hour = 0 if hour12 == 12 else hour12
        else:
            self.hour = 12 if hour12 == 12 else hour12 + 12

    def _display_time(self):
        if not self.time_12h:
            return "Not selected"
        return f"{self.time_12h} {self.period}"

    def content(self):
        game = f"{self.game.get('emoji') or '🎮'} {self.game['name']}" if self.game else "Not selected"
        date = datetime.fromisoformat(self.date).strftime("%A, %d %B %Y") if self.date else "Not selected"
        return (
            "# 🎮 Create GamerHQ Event\n\n"
            "Configure your event below. Your selections stay visible while you edit.\n\n"
            f"**📝 Title:** {self.title}\n"
            f"**🎮 Game:** {game}{' 🔒' if self.game_locked else ''}\n"
            f"**📅 Date:** {date}\n"
            f"**🕐 Time:** {self._display_time()} · Europe/Berlin\n"
            f"**👥 Players:** {self.max_players}\n"
            f"**🔔 Voice invite:** {self.invite_lead} min before\n"
            f"**👁️ Visibility:** {'🌐 Public' if self.visibility == 'public' else '🔒 Private · Invite only'}\n"
            "Discord will display the final event in each member's local time."
        )

    async def interaction_check(self, interaction):
        if interaction.user.id != self.host.id:
            await interaction.response.send_message("This event draft belongs to another user.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Title", emoji="✏️", style=discord.ButtonStyle.secondary, row=4)
    async def edit_title(self, interaction, button):
        await interaction.response.send_modal(EventTitleModal(self))

    @discord.ui.button(label="PM", emoji="🌓", style=discord.ButtonStyle.secondary, row=4)
    async def ampm_button(self, interaction, button):
        self.period = "AM" if self.period == "PM" else "PM"
        self._sync_24h_time()
        self.ampm_button.label = self.period
        self._rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="15m", emoji="🔔", style=discord.ButtonStyle.secondary, row=4)
    async def voice_lead(self, interaction, button):
        values = [5, 15, 30, 45]
        self.invite_lead = values[(values.index(self.invite_lead) + 1) % len(values)]
        self.voice_lead.label = f"{self.invite_lead}m"
        self._rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="Public", emoji="🌐", style=discord.ButtonStyle.secondary, row=4)
    async def visibility_button(self, interaction, button):
        self.visibility = "private" if self.visibility == "public" else "public"
        self._rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="Preview", emoji="👁️", style=discord.ButtonStyle.success, row=4)
    async def preview(self, interaction, button):
        if not self.game or not self.date or self.hour is None:
            return await interaction.response.send_message("❌ Choose a game, date and time first.", ephemeral=True)
        hour, minute = self.hour, self.minute
        dt = datetime.fromisoformat(self.date).replace(hour=hour, minute=minute, tzinfo=SERVER_TZ)
        if dt <= datetime.now(SERVER_TZ):
            return await interaction.response.send_message("❌ The event must be in the future.", ephemeral=True)
        draft = EventDraftView(self)
        await interaction.response.edit_message(content=draft.content(), view=draft)

    # Four selects occupy rows 0–3; row 4 has exactly five buttons.
    # Cancellation remains available in EventDraftView (or dismiss the draft).
    # Even a decorated button omitted by _rebuild is instantiated by View.__init__.


def game_for_lfg_channel(guild: discord.Guild, channel_id: int):
    for game in db.get_area_games(lfg_only=True):
        cid = game.get("lfg_channel_id") or game.get("clips_channel_id")
        if cid and int(cid) == int(channel_id):
            return game
    return None

class LFGHubView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Create Event",emoji="➕",style=discord.ButtonStyle.primary,custom_id="gamerhq:lfg:create")
    async def create_event(self, interaction, button):
        # A persistent hub button must acknowledge Discord immediately. DB lookups
        # and view construction happen after the defer so a slow disk or exception
        # can never surface as "The application did not respond".
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            return await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            games = user_games(interaction.user)
            if not games:
                return await interaction.edit_original_response(content="🎮 Select a game in Choose Your Games first.", view=None)
            channel_game = game_for_lfg_channel(interaction.guild, interaction.channel_id)
            if channel_game and not any(int(g["id"]) == int(channel_game["id"]) for g in games):
                return await interaction.edit_original_response(
                    content=f"🎮 Add **{channel_game['name']}** to your games first.", view=None
                )
            builder = EventBuilderView(
                host=interaction.user, games=games[:25], game=channel_game, game_locked=channel_game is not None
            )
            await interaction.edit_original_response(content=builder.content(), view=builder)
        except Exception as exc:
            print(f"[GamerHQ][LFG] Create Event button failed: {exc}")
            traceback.print_exc()
            await interaction.edit_original_response(
                content="❌ Event creation could not be opened. The error was logged for GamerHQ staff.", view=None
            )

def build_lfg_hub_view(): return LFGHubView()

class LFG(commands.Cog):
    lfg = app_commands.Group(name="lfg", description="Looking for Group events and tools.")

    def __init__(self,bot):
        self.bot=bot
        self.voice_scheduler.start()

    def cog_unload(self):
        self.voice_scheduler.cancel()

    @tasks.loop(seconds=30)
    async def voice_scheduler(self):
        now = int(datetime.now(SERVER_TZ).timestamp())
        for guild in self.bot.guilds:
            for event in db.get_active_lfg_events(guild.id):
                event_id = int(event["id"])
                voice_id = event.get("voice_channel_id")
                # Retire old events after a generous six-hour window. Never kick an
                # active voice room; cleanup waits until it is empty.
                if now >= int(event["start_at"]) + 6 * 3600:
                    voice = guild.get_channel(int(voice_id)) if voice_id else None
                    if isinstance(voice, discord.VoiceChannel) and voice.members:
                        continue
                    if isinstance(voice, discord.VoiceChannel):
                        try:
                            await voice.delete(reason=f"GamerHQ LFG event #{event_id} completed")
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            pass
                    await delete_event_posts(guild, event)
                    await delete_private_event_channel(guild, event)
                    db.set_lfg_event_status(event_id, "completed")
                    continue
                invite_at = int(event["start_at"]) - int(event["invite_lead_minutes"]) * 60
                if not voice_id and now >= invite_at and now < int(event["start_at"]) + 6 * 3600:
                    await create_event_voice(guild, event)
                    continue
                if voice_id:
                    channel = guild.get_channel(int(voice_id))
                    if channel is None:
                        db.clear_lfg_event_voice(event_id)
                    elif isinstance(channel, discord.VoiceChannel) and now >= int(event["start_at"]) + 2 * 3600 and not channel.members:
                        try:
                            await channel.delete(reason=f"GamerHQ LFG event #{event_id} finished and voice is empty")
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            pass
                        db.clear_lfg_event_voice(event_id)

    @voice_scheduler.before_loop
    async def before_voice_scheduler(self):
        await self.bot.wait_until_ready()

    async def _open_event_builder(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            return await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            games = user_games(interaction.user)
            if not games:
                return await interaction.edit_original_response(content="🎮 Select a game in Choose Your Games first.", view=None)
            channel_game = game_for_lfg_channel(interaction.guild, interaction.channel_id)
            if channel_game and not any(int(g["id"]) == int(channel_game["id"]) for g in games):
                return await interaction.edit_original_response(
                    content=f"🎮 Add **{channel_game['name']}** to your games first.", view=None
                )
            builder = EventBuilderView(
                host=interaction.user,
                games=games[:25],
                game=channel_game,
                game_locked=channel_game is not None,
            )
            await interaction.edit_original_response(content=builder.content(), view=builder)
        except Exception as exc:
            print(f"[GamerHQ][LFG] /lfg create failed: {exc}")
            traceback.print_exc()
            await interaction.edit_original_response(
                content="❌ Event creation could not be opened. The error was logged for GamerHQ staff.", view=None
            )

    @lfg.command(name="create", description="Create a GamerHQ Looking for Group event.")
    async def lfg_create(self, interaction: discord.Interaction):
        await self._open_event_builder(interaction)

    @lfg.command(name="join-code", description="Join a private GamerHQ event using its invite code.")
    @app_commands.describe(code="Private event invite code shared by the host")
    async def lfg_join_code(self, interaction: discord.Interaction, code: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ Join the GamerHQ server first, then use this command.", ephemeral=True)
        event = db.get_lfg_event_by_share_token(code.strip())
        if not event or int(event["guild_id"]) != interaction.guild.id or event.get("visibility") != "private":
            return await interaction.response.send_message("❌ This private event invite is invalid, disabled, or expired.", ephemeral=True)
        game = db.get_game_by_id(int(event["game_id"]))
        if not game:
            return await interaction.response.send_message("❌ This event's game is unavailable.", ephemeral=True)
        if not member_has_game_role(interaction.user, game):
            return await prompt_add_game_and_join(interaction, event, game, interaction.guild)
        result = await _finish_join(interaction.guild, event, interaction.user)
        if result == "full":
            return await interaction.response.send_message("❌ This event is full.", ephemeral=True)
        if result == "already":
            return await interaction.response.send_message("✅ You're already in this private event.", ephemeral=True)
        await interaction.response.send_message("✅ You've joined the private event and now have access to its event channel.", ephemeral=True)

    @lfg.command(name="manage", description="Manage or cancel LFG events you created.")
    async def lfg_manage(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ This can only be used inside GamerHQ.", ephemeral=True)
        events = [
            event for event in db.get_active_lfg_events(interaction.guild.id)
            if int(event["host_id"]) == interaction.user.id and event.get("status") == "scheduled"
        ]
        events.sort(key=lambda event: int(event["start_at"]))
        if not events:
            return await interaction.response.send_message("🎮 You don't currently have any active LFG events to manage.", ephemeral=True)
        await interaction.response.send_message(
            "# ⚙️ Manage Your LFG Events\n\nChoose one of your events below. Only you can see this panel.",
            view=LFGManageView(interaction.user.id, events),
            ephemeral=True,
        )
    async def cog_load(self):
        self.bot.add_view(LFGHubView())
        for event in db.get_active_lfg_events():
            rows=db.get_lfg_event_messages(event["id"])
            if rows:
                for row in rows: self.bot.add_view(LFGEventView(event["id"]),message_id=int(row["message_id"]))
            elif event.get("message_id"): self.bot.add_view(LFGEventView(event["id"]),message_id=int(event["message_id"]))
            # Keep DM invite Join buttons working after bot restarts.
            self.bot.add_view(LFGInviteDMView(int(event["id"]), int(event["guild_id"])))

    @commands.Cog.listener()
    async def on_ready(self):
        if getattr(self, "_game_lfg_migrated", False):
            return
        self._game_lfg_migrated = True
        for guild in self.bot.guilds:
            for game in db.get_area_games(lfg_only=True):
                cid=game.get("lfg_channel_id") or game.get("clips_channel_id")
                channel=guild.get_channel(int(cid)) if cid else None
                if isinstance(channel,discord.TextChannel) and "clips" in channel.name:
                    try:
                        await channel.edit(name="🎯・looking-for-group",reason="GamerHQ LFG migration: replace clips with game LFG")
                    except (discord.Forbidden,discord.HTTPException):
                        pass

async def setup(bot): await bot.add_cog(LFG(bot))
