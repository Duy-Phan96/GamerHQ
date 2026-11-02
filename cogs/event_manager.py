"""Discord interaction adapter for the installed Event Manager's public API.

No Event persistence, recurrence or participant policy is implemented here.
Host authorization and rendering wrap the Skill-owned management operations.
"""
from __future__ import annotations

import json
import re
import secrets

import discord
from discord import app_commands
from discord.ext import commands

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
        contract_id=f"event-manager.{operation}.v1", payload=payload)


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
        draft["members"] = str(interaction.user.id)
    schema = interaction.client.skill_runtime.management_ui_schema(SKILL)
    collection = next(field.collection for section in schema.sections for field in section.fields if field.collection)
    view = SkillEventEditor(interaction.user.id, collection.item_fields, draft, event_id=event_id, revision=revision)
    await interaction.followup.send(content=view.content(), view=view, ephemeral=True)


class FieldModal(discord.ui.Modal):
    def __init__(self, editor, field):
        super().__init__(title=field.label[:45])
        self.editor, self.field = editor, field
        value = editor.draft.get(field.config_path, "")
        if isinstance(value, list):
            value = ",".join(map(str, value))
        if len(str(value)) > 4000:
            raise ValueError("This field is too large for a Discord modal; edit it through Web.")
        self.value = discord.ui.TextInput(label=field.label[:45], default=str(value), required=field.required,
            max_length=4000 if field.type == "long_text" else 100,
            style=discord.TextStyle.paragraph if field.type == "long_text" else discord.TextStyle.short)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        if not await self.editor.interaction_check(interaction):
            return
        value = str(self.value)
        try:
            if self.field.type == "integer":
                value = int(value)
            self.editor.draft[self.field.config_path] = value
            self.editor.reviewed = None
            await interaction.response.edit_message(content=self.editor.content(), view=self.editor)
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)


class WeekdayButton(discord.ui.Button):
    def __init__(self, editor, field, option, index):
        self.editor, self.field, self.option = editor, field, option
        selected = {str(value) for value in editor.draft.get(field.config_path, [])}
        super().__init__(label=option.label, row=1 + index // 4,
            style=discord.ButtonStyle.primary if option.value in selected else discord.ButtonStyle.secondary)
    async def callback(self, interaction):
        if not await self.editor.interaction_check(interaction):
            return
        selected = {str(value) for value in self.editor.draft.get(self.field.config_path, [])}
        selected.symmetric_difference_update((self.option.value,))
        self.editor.draft[self.field.config_path] = sorted(selected)
        self.editor.reviewed = None
        self.editor.rebuild(self.field)
        await interaction.response.edit_message(content=self.editor.content(), view=self.editor)


class FieldSelect(discord.ui.Select):
    def __init__(self, editor):
        self.editor = editor
        super().__init__(placeholder="Choose a setting to edit", row=0,
            options=[discord.SelectOption(label=field.label[:100], value=str(index)) for index, field in enumerate(editor.fields)])
    async def callback(self, interaction):
        if not await self.editor.interaction_check(interaction):
            return
        field = self.editor.fields[int(self.values[0])]
        if field.type == "boolean":
            self.editor.draft[field.config_path] = not bool(self.editor.draft.get(field.config_path))
            self.editor.reviewed = None
        elif field.type == "multi_select":
            self.editor.rebuild(field)
        elif field.type == "select":
            view = self.editor
            select = discord.ui.Select(placeholder=field.label[:150], row=1,
                options=[discord.SelectOption(label=option.label, value=option.value,
                    default=option.value == str(view.draft.get(field.config_path))) for option in field.options])
            async def change(choice):
                if not await view.interaction_check(choice):
                    return
                view.draft[field.config_path] = select.values[0]
                view.reviewed = None
                view.rebuild()
                await choice.response.edit_message(content=view.content(), view=view)
            select.callback = change
            view.rebuild()
            view.add_item(select)
        else:
            try:
                return await interaction.response.send_modal(FieldModal(self.editor, field))
            except ValueError as error:
                return await interaction.response.send_message(str(error), ephemeral=True)
        await interaction.response.edit_message(content=self.editor.content(), view=self.editor)


class SkillEventEditor(discord.ui.View):
    def __init__(self, actor_id, fields, draft, *, event_id=None, revision=None):
        super().__init__(timeout=900)
        self.actor_id, self.fields, self.draft = actor_id, fields, draft
        self.event_id, self.revision = event_id, revision
        self.request_id, self.reviewed = secrets.token_hex(16), None
        self.rebuild()

    def rebuild(self, weekday_field=None):
        self.clear_items()
        self.add_item(FieldSelect(self))
        if weekday_field:
            for index, option in enumerate(weekday_field.options):
                self.add_item(WeekdayButton(self, weekday_field, option, index))
        for item in (self.preview, self.save, self.cancel):
            self.add_item(item)

    def content(self):
        rows = []
        for field in self.fields:
            value = self.draft.get(field.config_path, "")
            if field.type in ("select", "multi_select"):
                selected = {str(item) for item in value} if isinstance(value, list) else {str(value)}
                value = ", ".join(option.label for option in field.options if option.value in selected)
            rows.append(f"**{field.label}:** {str(value)[:130] or 'Not set'}")
        return ("# Event Manager — " + ("Edit" if self.event_id else "Create") + "\n" + "\n".join(rows) +
            "\n\nPreview validates the complete draft. Confirm saves through the installed Skill.")[:1900]

    async def interaction_check(self, interaction):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("This editor belongs to another member.", ephemeral=True)
            return False
        if self.event_id:
            state = (await call(interaction, "get", {"eventId": self.event_id}))["state"]
            if not can_manage(interaction.user, state):
                await interaction.response.send_message("Your event management permission changed.", ephemeral=True)
                return False
        return True

    def payload(self):
        return {**self.draft, **({"eventId": self.event_id, "revision": self.revision} if self.event_id else
            {"requestId": self.request_id, "ownerId": str(self.actor_id)})}

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.primary, row=4)
    async def preview(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await call(interaction, "validate", self.payload())
            self.reviewed = json.dumps(self.draft, sort_keys=True)
            next_at = result.get("nextOccurrence")
            await interaction.followup.send(result["summary"] + (f"\nNext: <t:{next_at}:F>" if next_at else "\nNo future occurrence."), ephemeral=True)
        except (ValueError, RuntimeError) as error:
            self.reviewed = None
            await interaction.followup.send(str(error)[:1500], ephemeral=True)

    @discord.ui.button(label="Confirm save", style=discord.ButtonStyle.success, row=4)
    async def save(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        if self.reviewed != json.dumps(self.draft, sort_keys=True):
            return await interaction.response.send_message("Preview the current draft before confirming.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            result = await call(interaction, "update" if self.event_id else "create", self.payload())
            event = result["event"]
            try:
                await call(interaction, "reconcile", {"eventId": event["id"]})
                message = "Event saved and its persistent area synchronized."
            except Exception:
                message = "Event saved. Discord synchronization is pending; check Skill health before retrying."
            await interaction.edit_original_response(content=message, view=None)
            self.stop()
        except (ValueError, RuntimeError) as error:
            await interaction.followup.send(str(error)[:1500], ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=4)
    async def cancel(self, interaction, button):
        if await self.interaction_check(interaction):
            await interaction.response.edit_message(content="Draft discarded.", view=None)
            self.stop()


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
        events = (await call(interaction, "list", {}))["events"]
        visible = [event for event in events if can_view(interaction.user, event)]
        await interaction.followup.send("\n".join(f"`{event['id']}` · {event['config']['name']} · {event['status']}" for event in visible[:25]) or "No events available.", ephemeral=True)

    async def member_action(self, interaction, event_id, action, *, start_at=None):
        state = (await call(interaction, "get", {"eventId": event_id}))["state"]
        if not can_view(interaction.user, state):
            raise PermissionError("This private event is not available to you.")
        history = await call(interaction, "history", {"eventId": event_id})
        available = [row for row in history["occurrences"] if row["status"] == "scheduled" and (start_at is None or row["startAt"] == start_at)]
        if not available:
            raise ValueError("No scheduled occurrence is available.")
        row = min(available, key=lambda item: item["startAt"])
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
