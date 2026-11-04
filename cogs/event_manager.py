"""Discord interaction adapter for the installed Event Manager's public API.

No Event persistence, recurrence or participant policy is implemented here.
Host authorization and rendering wrap the Skill-owned management operations.
"""
from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from cogs.event_editor import SkillEventEditor

SKILL = "event-manager"


def can_manage(member, event):
    rights = member.guild_permissions
    return bool(rights.administrator or rights.manage_guild or str(member.id) == event.get("ownerId"))


def can_view(member, event):
    return can_manage(member, event) or event["config"]["visibility"] == "public" or str(member.id) in event["members"] + event["invited"]


async def call(interaction, operation, payload):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        raise PermissionError("Use Event Manager as a member of this server.")
    runtime = interaction.client.skill_runtime
    if not runtime.registry.contains(SKILL):
        raise ValueError("The reviewed Event Manager package is unavailable in this Host.")
    return await runtime.call_management(guild_id=interaction.guild.id, skill_id=SKILL,
        contract_id=f"event-manager.{operation}.v1", payload=payload, actor_id=interaction.user.id)


async def open_editor(interaction, event_id=None):
    if event_id:
        result = await call(interaction, "get", {"eventId": event_id})
        if not can_manage(interaction.user, result["state"]):
            raise PermissionError("Only the event owner or a server administrator can edit this event.")
        draft, revision = dict(result["event"]), result["event"]["revision"]
        draft.pop("id", None)
        draft.pop("revision", None)
    else:
        result = await call(interaction, "describe", {})
        draft, revision = dict(result["defaultDraft"]), None
        from services.lfg_service import SERVER_TZ
        draft["timezone"] = str(SERVER_TZ)
        draft["members"] = str(interaction.user.id)
    schema = interaction.client.skill_runtime.management_ui_schema(SKILL)
    collection = next(field.collection for section in schema.sections for field in section.fields if field.collection)
    view = SkillEventEditor(interaction.user.id, collection.item_fields, draft, event_id=event_id, revision=revision,
        guild_id=interaction.guild.id, legacy_schedule=bool(event_id and result["state"].get("legacyRecurrence")))
    await interaction.followup.send(content=view.content(), view=view, ephemeral=True)


class EventSkillHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Create Event", style=discord.ButtonStyle.primary, custom_id="gamerhq:lfg:create")
    async def create(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            await open_editor(interaction)
        except (ValueError, PermissionError, RuntimeError) as error:
            await interaction.followup.send(str(error)[:1500], ephemeral=True)


class EventManager(commands.Cog):
    lfg = app_commands.Group(name="lfg", description="Manage events through the installed Event Manager Skill.")
    def __init__(self, bot):
        self.bot = bot

    @lfg.command(name="create", description="Create a one-time or recurring event.")
    async def create(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await open_editor(interaction)

    @lfg.command(name="edit", description="Edit all event settings with a shared preview and confirmation.")
    async def edit(self, interaction: discord.Interaction, event_id: str):
        await interaction.response.defer(ephemeral=True)
        await open_editor(interaction, event_id)

    @lfg.command(name="manage", description="List events you may view and manage.")
    async def manage(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await call(interaction, "list", {})
        participant_events = {row["eventId"] for row in result.get("occurrences", [])
            if row["status"] in ("scheduled", "active") and str(interaction.user.id) in row["participants"]}
        visible = [event for event in result["events"] if can_view(interaction.user, event) or event["id"] in participant_events]
        await interaction.followup.send("\n".join(f"`{event['id']}` · {event['config']['name']} · {event['status']}" for event in visible[:25]) or "No events available.", ephemeral=True)

    async def member_action(self, interaction, event_id, action, *, start_at=None):
        state = (await call(interaction, "get", {"eventId": event_id}))["state"]
        history = await call(interaction, "history", {"eventId": event_id})
        available = [row for row in history["occurrences"] if row["status"] in ("scheduled", "active") and (start_at is None or row["startAt"] == start_at)]
        if not available:
            raise ValueError("No scheduled occurrence is available.")
        row = min(available, key=lambda item: item["startAt"])
        if not can_view(interaction.user, state) and str(interaction.user.id) not in row["participants"]:
            raise PermissionError("This private event is not available to you.")
        members = set(row["participants"])
        if action == "join":
            members.add(str(interaction.user.id))
        else:
            members.discard(str(interaction.user.id))
        await call(interaction, "participate", {"eventId": event_id, "startAt": row["startAt"], "members": sorted(members), "revision": row["revision"]})
        await interaction.followup.send("Participation saved for this occurrence. Discord access will be reconciled.", ephemeral=True)

    @lfg.command(name="join", description="Join the next occurrence of an event.")
    async def join(self, interaction: discord.Interaction, event_id: str):
        await interaction.response.defer(ephemeral=True)
        await self.member_action(interaction, event_id, "join")

    @lfg.command(name="leave", description="Leave the next occurrence of an event.")
    async def leave(self, interaction: discord.Interaction, event_id: str):
        await interaction.response.defer(ephemeral=True)
        await self.member_action(interaction, event_id, "leave")

    @lfg.command(name="participants", description="Replace participants for the next occurrence only.")
    async def participants(self, interaction: discord.Interaction, event_id: str, member_ids: str):
        await interaction.response.defer(ephemeral=True)
        state = (await call(interaction, "get", {"eventId": event_id}))["state"]
        if not can_manage(interaction.user, state):
            raise PermissionError("Only the event owner or a server administrator may manage participants.")
        rows = (await call(interaction, "history", {"eventId": event_id}))["occurrences"]
        available = [row for row in rows if row["status"] in ("scheduled", "active")]
        if not available:
            raise ValueError("No occurrence is available.")
        row = min(available, key=lambda value: value["startAt"])
        await call(interaction, "occurrences-update", {"occurrenceId": row["id"], "revision": row["revision"], "participants": member_ids})
        await interaction.followup.send("Participants saved for this occurrence. Discord access will be reconciled.", ephemeral=True)

    @lfg.command(name="join-code", description="Join a migrated private occurrence using its existing invitation code.")
    async def join_code(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        await call(interaction, "join-code", {"code": code.strip()})
        await interaction.followup.send("Participation saved. Discord access will be reconciled.", ephemeral=True)

    @lfg.command(name="active", description="Pause or resume this event without deleting its area or history.")
    async def active(self, interaction: discord.Interaction, event_id: str, active: bool):
        await interaction.response.defer(ephemeral=True)
        state = (await call(interaction, "get", {"eventId": event_id}))["state"]
        if not can_manage(interaction.user, state):
            raise PermissionError("Only the event owner or a server administrator may change this event.")
        await call(interaction, "set-active", {"eventId": event_id, "revision": state["revision"], "active": active})
        await interaction.followup.send("Event resumed." if active else "Event paused. Its area and history are retained.", ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        data = interaction.data or {}
        match = re.fullmatch(r"gamerhq:lfg:(join|leave):([0-9]+)", str(data.get("custom_id", "")))
        if not match or interaction.response.is_done():
            return
        await interaction.response.defer(ephemeral=True)
        try:
            alias = await call(interaction, "resolve-legacy", {"legacyEventId": int(match[2])})
            if not alias:
                raise ValueError("Legacy reference is unavailable; use /lfg manage.")
            await self.member_action(interaction, alias["eventId"], match[1], start_at=alias["startAt"])
        except (ValueError, PermissionError, RuntimeError) as error:
            await interaction.followup.send(str(error)[:1500], ephemeral=True)

    async def cog_app_command_error(self, interaction, error):
        message = "Event Manager could not complete this operation. Check the current event state and Skill health."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot):
    bot.add_view(EventSkillHubView())
    await bot.add_cog(EventManager(bot))
