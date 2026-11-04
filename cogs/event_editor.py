"""Established Discord event builder, backed exclusively by public Skill APIs.

Only transient drafts and presentation live here. Reuse the V1 builder controls,
never its persistence, recurrence engine, channel creation or lifecycle handlers.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from cogs.lfg import (BuilderDateSelect, BuilderTimeSelect, BuilderPlayersSelect,
    BuilderRecurrenceSelect, EventTitleModal, WeekdayToggle)


async def api(interaction, operation, payload):
    from cogs.event_manager import call
    return await call(interaction, operation, payload)


def safe(value, limit=200):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


def selected_ids(value):
    # Presentation only. Validation and membership policy remain in the Skill.
    return set(value.split(",")) - {""} if isinstance(value, str) else set(map(str, value))


class GuardedControl:
    """Keep reused callbacks behind the current editor's authorization check."""
    async def callback(self, interaction):
        if await self.builder.interaction_check(interaction):
            await super().callback(interaction)


class DateSelect(GuardedControl, BuilderDateSelect):
    pass


class TimeSelect(GuardedControl, BuilderTimeSelect):
    pass


class PlayersSelect(GuardedControl, BuilderPlayersSelect):
    pass


class RecurrenceSelect(BuilderRecurrenceSelect):
    async def callback(self, interaction):
        if not await self.builder.interaction_check(interaction):
            return
        if self.values[0] == "once":
            self.builder.draft.update(kind="once", weekdays=[])
            self.builder.rebuild()
            return await interaction.response.edit_message(content=self.builder.content(), view=self.builder)
        page = WeekdayView(self.builder)
        await interaction.response.edit_message(content=page.content(), view=page)


class WeekdayView(discord.ui.View):
    def __init__(self, builder):
        super().__init__(timeout=900)
        self.builder = builder
        self.selected = {int(day) for day in builder.draft.get("weekdays", [])}
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        for day in range(7):
            self.add_item(WeekdayToggle(self, day))
        self.add_item(self.apply_days)
        self.add_item(self.back)

    def content(self):
        names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        days = ", ".join(names[day] for day in sorted(self.selected)) or "Choose at least one weekday"
        retention = ("An imported schedule and its end condition are retained until you explicitly confirm replacement in event settings."
            if self.builder.legacy_schedule else "Repeats until you edit, pause or delete it.")
        return (f"# Recurring Event\n\nSelect each weekday independently.\n\n**Every:** {days}\n"
            f"**Time:** {self.builder._display_time()} · {safe(self.builder.timezone_name)}\n\n"
            f"{retention} Nothing has been saved yet.")

    async def interaction_check(self, interaction):
        return await self.builder.interaction_check(interaction)

    @discord.ui.button(label="Apply weekdays", style=discord.ButtonStyle.success, row=2)
    async def apply_days(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        if not self.selected:
            return await interaction.response.send_message("Choose at least one weekday.", ephemeral=True)
        self.builder.draft.update(kind="recurring", weekdays=sorted(self.selected))
        self.builder.rebuild()
        await interaction.response.edit_message(content=self.builder.content(), view=self.builder)
        self.stop()

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction, button):
        if await self.interaction_check(interaction):
            await interaction.response.edit_message(content=self.builder.content(), view=self.builder)
            self.stop()


class DetailsModal(EventTitleModal):
    """Extend the familiar Title modal instead of inventing another primary flow."""
    def __init__(self, builder):
        super().__init__(builder)
        self.title_input.max_length = 100
        self.title_input.default = builder.title
        self.game = discord.ui.TextInput(label="Game", default=builder.draft.get("game", ""), required=False, max_length=100)
        self.description = discord.ui.TextInput(label="Description", default=builder.draft.get("description", ""),
            required=False, max_length=4000, style=discord.TextStyle.paragraph)
        self.add_item(self.game)
        self.add_item(self.description)

    async def on_submit(self, interaction):
        if not await self.builder.interaction_check(interaction):
            return
        if not str(self.title_input).strip():
            return await interaction.response.send_message("Please enter an event title.", ephemeral=True)
        self.builder.draft.update(name=str(self.title_input).strip(), game=str(self.game), description=str(self.description))
        self.builder.rebuild()
        await interaction.response.edit_message(content=self.builder.content(), view=self.builder)


class SettingModal(discord.ui.Modal):
    def __init__(self, page, field):
        super().__init__(title=page.label(field)[:45])
        self.page, self.field = page, field
        self.value = discord.ui.TextInput(label=page.label(field)[:45], default=str(page.builder.draft.get(field.config_path, "")),
            required=field.required, max_length=4000 if field.type == "long_text" else 100,
            style=discord.TextStyle.paragraph if field.type == "long_text" else discord.TextStyle.short)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        if not await self.page.interaction_check(interaction):
            return
        try:
            value = int(str(self.value)) if self.field.type == "integer" else str(self.value)
        except ValueError:
            return await interaction.response.send_message("Enter a whole number.", ephemeral=True)
        try:
            if self.field.config_path == "startDate" and date.fromisoformat(value).isoformat() != value:
                raise ValueError()
            if self.field.config_path == "localTime":
                wall = time.fromisoformat(value)
                if len(value) != 5 or wall.tzinfo or wall.second or wall.microsecond:
                    raise ValueError()
            if self.field.config_path == "timezone":
                ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            return await interaction.response.send_message("Use YYYY-MM-DD for dates, HH:MM for time, and an IANA timezone such as Europe/Berlin.", ephemeral=True)
        self.page.builder.draft[self.field.config_path] = value
        self.page.builder.reviewed = None
        await interaction.response.edit_message(content=self.page.content(), view=self.page)


class SettingsView(discord.ui.View):
    """All remaining declared capabilities are opt-in, never a raw draft dump."""
    def __init__(self, builder):
        super().__init__(timeout=900)
        self.builder = builder
        fields = [field for field in builder.fields if field.config_path not in ("members", "invited", "kind", "weekdays")]
        select = discord.ui.Select(placeholder="Choose an event setting", row=0,
            options=[discord.SelectOption(label=self.label(field), value=str(index)) for index, field in enumerate(fields)])

        async def choose(interaction):
            if not await self.interaction_check(interaction):
                return
            field = fields[int(select.values[0])]
            if field.type == "boolean":
                builder.draft[field.config_path] = not bool(builder.draft.get(field.config_path))
                builder.reviewed = None
                await interaction.response.edit_message(content=self.content(), view=self)
            elif field.type == "select":
                values = discord.ui.Select(placeholder=self.label(field), row=1,
                    options=[discord.SelectOption(label=option.label, value=option.value,
                        default=option.value == str(builder.draft.get(field.config_path))) for option in field.options])
                async def change(choice):
                    if not await self.interaction_check(choice):
                        return
                    builder.draft[field.config_path] = values.values[0]
                    builder.reviewed = None
                    self.remove_item(values)
                    await choice.response.edit_message(content=self.content(), view=self)
                values.callback = change
                for child in list(self.children):
                    if child.row == 1:
                        self.remove_item(child)
                self.add_item(values)
                await interaction.response.edit_message(content=self.content(), view=self)
            else:
                await interaction.response.send_modal(SettingModal(self, field))
        select.callback = choose
        self.add_item(select)

    @staticmethod
    def label(field):
        return {"startDate": "Date", "localTime": "Time", "maxPlayers": "Player limit",
            "voiceEnabled": "Voice reminders", "voiceLeadMinutes": "Voice reminder lead time",
            "chatEnabled": "Event chat", "convertLegacySchedule": "Replace imported schedule and end condition"}.get(field.config_path, field.label)[:100]

    def content(self):
        b = self.builder
        lines = ["# Event settings", "Choose a setting to adjust it. Preview again before saving.", b.content()]
        for field in b.fields:
            key = field.config_path
            if key in ("durationMinutes", "autoEnroll", "voiceEnabled", "chatEnabled", "convertLegacySchedule"):
                value = b.draft.get(key, False)
                lines.append(f"**{self.label(field)}:** {'On' if value is True else 'Off' if value is False else safe(value)}")
        if b.draft.get("convertLegacySchedule"):
            lines.append("⚠️ Confirmation selected: replace the imported schedule and its existing end condition when saved.")
        return "\n".join(lines)[:1900]

    async def interaction_check(self, interaction):
        return await self.builder.interaction_check(interaction)

    @discord.ui.button(label="Back to event", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction, button):
        if await self.interaction_check(interaction):
            self.builder.rebuild()
            await interaction.response.edit_message(content=self.builder.content(), view=self.builder)


class ParticipantSelect(discord.ui.UserSelect):
    def __init__(self, page, field, remove, row, label):
        super().__init__(placeholder=label, min_values=0, max_values=25, row=row)
        self.page, self.field, self.remove = page, field, remove

    async def callback(self, interaction):
        if not await self.page.interaction_check(interaction):
            return
        b = self.page.builder
        current = selected_ids(b.draft.get(self.field, ""))
        chosen = {str(member.id) for member in self.values if self.remove or not member.bot}
        current = current - chosen if self.remove else current | chosen
        b.draft[self.field] = ",".join(sorted(current))
        b.reviewed = None
        await interaction.response.edit_message(content=self.page.content(), view=self.page)


class ParticipantsView(discord.ui.View):
    def __init__(self, builder):
        super().__init__(timeout=900)
        self.builder = builder
        for row, (field, remove, label) in enumerate((("members", False, "Add event players"),
                ("members", True, "Remove event players"), ("invited", False, "Invite players"),
                ("invited", True, "Remove invitations"))):
            self.add_item(ParticipantSelect(self, field, remove, row, label))

    def content(self):
        b = self.builder
        players = ", ".join(f"<@{ident}>" for ident in sorted(selected_ids(b.draft.get("members", "")))[:15]) or "None"
        invited = ", ".join(f"<@{ident}>" for ident in sorted(selected_ids(b.draft.get("invited", "")))[:15]) or "None"
        return (f"# Event players\n\n**Players:** {len(selected_ids(b.draft.get('members', '')))}\n"
            f"**Invited:** {len(selected_ids(b.draft.get('invited', '')))}\n\n"
            f"**Players (first 15):** {players}\n**Invited (first 15):** {invited}\n\n"
            "Add or remove players by name. Existing players are retained. Preview again before saving.\n"
            "These are event members and invitations. Use Join/Leave or /lfg participants for a single occurrence.")

    async def interaction_check(self, interaction):
        return await self.builder.interaction_check(interaction)

    @discord.ui.button(label="Back to event", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction, button):
        if await self.interaction_check(interaction):
            self.builder.rebuild()
            await interaction.response.edit_message(content=self.builder.content(), view=self.builder)


class EventPreviewView(discord.ui.View):
    def __init__(self, builder):
        super().__init__(timeout=900)
        self.builder = builder
        self.confirm.label = "Save changes" if builder.event_id else "Create Event"
        self.confirm.disabled = builder.reviewed is None

    async def interaction_check(self, interaction):
        return await self.builder.interaction_check(interaction)

    @discord.ui.button(label="Edit", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
    async def edit(self, interaction, button):
        if await self.interaction_check(interaction):
            self.builder.rebuild()
            await interaction.response.edit_message(content=self.builder.content(), view=self.builder)

    @discord.ui.button(label="Create Event", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def confirm(self, interaction, button):
        await self.builder.save(interaction)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction, button):
        if await self.interaction_check(interaction):
            self.builder.submitted = True
            await interaction.response.edit_message(content="Draft discarded.", view=None)
            self.builder.stop()
            self.stop()

    @discord.ui.button(label="Players & invites", style=discord.ButtonStyle.secondary, row=2)
    async def participants(self, interaction, button):
        if await self.interaction_check(interaction):
            view = ParticipantsView(self.builder)
            await interaction.response.edit_message(content=view.content(), view=view)

    @discord.ui.button(label="Event settings", style=discord.ButtonStyle.secondary, row=2)
    async def settings(self, interaction, button):
        if await self.interaction_check(interaction):
            view = SettingsView(self.builder)
            await interaction.response.edit_message(content=view.content(), view=view)


class SkillEventEditor(discord.ui.View):
    def __init__(self, actor_id, fields, draft, *, event_id=None, revision=None, guild_id=None, legacy_schedule=False):
        super().__init__(timeout=900)
        self.actor_id, self.fields, self.draft, self.guild_id = actor_id, fields, draft, guild_id
        self.event_id, self.revision = event_id, revision
        self.legacy_schedule = legacy_schedule
        self.request_id, self.reviewed = secrets.token_hex(16), None
        self.submitted, self.saving = False, asyncio.Lock()
        self.rebuild()

    # Transient presentation properties expected by the existing builder controls.
    title = property(lambda s: s.draft.get("name", ""), lambda s, v: s.draft.__setitem__("name", v))
    date = property(lambda s: s.draft.get("startDate"), lambda s, v: s.draft.__setitem__("startDate", v))
    max_players = property(lambda s: s.draft.get("maxPlayers", 5), lambda s, v: s.draft.__setitem__("maxPlayers", v))
    timezone_name = property(lambda s: s.draft.get("timezone", "UTC"))
    recurrence_mode = property(lambda s: s.draft.get("kind", "once"))
    hour = property(lambda s: int(s.draft.get("localTime", "19:00").split(":")[0]))
    minute = property(lambda s: int(s.draft.get("localTime", "19:00").split(":")[1]))
    period = property(lambda s: "AM" if s.hour < 12 else "PM")
    time_12h = property(lambda s: f"{s.hour % 12 or 12}:{s.minute:02d}", lambda s, v: s.set_time(v, s.period))

    def set_time(self, clock, period):
        hour, minute = map(int, clock.split(":"))
        self.draft["localTime"] = f"{hour % 12 + (12 if period == 'PM' else 0):02d}:{minute:02d}"

    def _sync_24h_time(self):
        pass  # Reused time selector already wrote the wall time through the property.

    def _display_time(self):
        return f"{self.time_12h} {self.period}"

    def recurrence_label(self):
        if self.recurrence_mode == "once":
            return "One-time"
        field = next((field for field in self.fields if field.config_path == "weekdays"), None)
        selected = set(map(str, self.draft.get("weekdays", [])))
        days = ", ".join(option.label for option in field.options if option.value in selected) if field else ""
        return f"Every {days}" if days else "Choose weekdays"

    def rebuild(self):
        self.reviewed = None
        self.clear_items()
        for control in (DateSelect(self), TimeSelect(self), PlayersSelect(self), RecurrenceSelect(self)):
            self.add_item(control)
        self.ampm_button.label = self.period
        self.voice_lead.label = f"{self.draft.get('voiceLeadMinutes', 15)}m" if self.draft.get("voiceEnabled") else "Voice off"
        self.visibility_button.label = "Public" if self.draft.get("visibility") == "public" else "Private"
        self.visibility_button.emoji = "🌐" if self.draft.get("visibility") == "public" else "🔒"
        for button in (self.edit_title, self.ampm_button, self.voice_lead, self.visibility_button, self.preview):
            self.add_item(button)

    _rebuild = rebuild

    def content(self):
        date = datetime.fromisoformat(self.date).strftime("%A, %d %B %Y") if self.date else "Not selected"
        lead = f"{self.draft.get('voiceLeadMinutes', 15)} min before" if self.draft.get("voiceEnabled") else "Off"
        return (f"# 📅 {'Edit' if self.event_id else 'Create'} GamerHQ Event\n\n"
            f"**📝 Title:** {safe(self.title, 100)}\n"
            + (f"**Game:** {safe(self.draft['game'], 100)}\n" if self.draft.get("game") else "")
            + (f"{safe(self.draft['description'], 180)}\n" if self.draft.get("description") else "")
            + f"**📅 Date:** {safe(date)}\n**🕐 Time:** {self._display_time()} · {safe(self.timezone_name)}\n"
            f"**👥 Players:** {self.max_players}\n**🔁 Repeat:** {self.recurrence_label()}\n"
            f"**🔔 Voice invite:** {lead}\n**👁️ Visibility:** {'🌐 Public' if self.draft.get('visibility') == 'public' else '🔒 Private · Invite only'}\n"
            "Discord will display the final event in each member's local time.")

    async def interaction_check(self, interaction):
        if (interaction.user.id != self.actor_id or
                (self.guild_id is not None and getattr(interaction.guild, "id", None) != self.guild_id)):
            await interaction.response.send_message("This editor belongs to another member or server.", ephemeral=True)
            return False
        if self.submitted:
            await interaction.response.send_message("This draft has already been saved or discarded. Open /lfg manage.", ephemeral=True)
            return False
        if self.saving.locked():
            await interaction.response.send_message("Saving this draft. Please wait for the result.", ephemeral=True)
            return False
        if self.event_id:
            from cogs.event_manager import can_manage
            state = (await api(interaction, "get", {"eventId": self.event_id}))["state"]
            if not can_manage(interaction.user, state):
                await interaction.response.send_message("Your event management permission changed.", ephemeral=True)
                return False
        return True

    def payload(self):
        return {**self.draft, **({"eventId": self.event_id, "revision": self.revision} if self.event_id else
            {"requestId": self.request_id, "ownerId": str(self.actor_id)})}

    @discord.ui.button(label="Title", emoji="✏️", style=discord.ButtonStyle.secondary, row=4)
    async def edit_title(self, interaction, button):
        if await self.interaction_check(interaction):
            await interaction.response.send_modal(DetailsModal(self))

    @discord.ui.button(label="PM", emoji="🌓", style=discord.ButtonStyle.secondary, row=4)
    async def ampm_button(self, interaction, button):
        if await self.interaction_check(interaction):
            self.set_time(self.time_12h, "AM" if self.period == "PM" else "PM")
            self.rebuild()
            await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="Voice off", emoji="🔔", style=discord.ButtonStyle.secondary, row=4)
    async def voice_lead(self, interaction, button):
        if await self.interaction_check(interaction):
            values = [None, 15, 30, 45, 5]
            current = self.draft.get("voiceLeadMinutes", 15) if self.draft.get("voiceEnabled") else None
            lead = values[(values.index(current) + 1) % len(values)] if current in values else None
            self.draft["voiceEnabled"] = lead is not None
            if lead is not None:
                self.draft["voiceLeadMinutes"] = lead
            self.rebuild()
            await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="Private", emoji="🔒", style=discord.ButtonStyle.secondary, row=4)
    async def visibility_button(self, interaction, button):
        if await self.interaction_check(interaction):
            self.draft["visibility"] = "private" if self.draft.get("visibility") == "public" else "public"
            self.rebuild()
            await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(label="Preview", emoji="👁️", style=discord.ButtonStyle.success, row=4)
    async def preview(self, interaction, button):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        draft = json.dumps(self.draft, sort_keys=True)
        try:
            result = await api(interaction, "validate", self.payload())
            if draft != json.dumps(self.draft, sort_keys=True):
                return await interaction.followup.send("The draft changed. Preview it again.", ephemeral=True)
            self.reviewed = draft
            next_at = result.get("nextOccurrence")
            if self.event_id and self.legacy_schedule and not self.draft.get("convertLegacySchedule"):
                # The Skill's retained finite schedule, never a Host recurrence calculation.
                existing = await api(interaction, "get", {"eventId": self.event_id})
                next_at = existing["state"].get("nextOccurrence")
            content = self.content().replace("Create GamerHQ Event", "Event Preview").replace("Edit GamerHQ Event", "Event Preview")
            content += (f"\n\n**Start:** <t:{next_at}:F> (<t:{next_at}:R>)" if next_at else "\n\nNo future occurrence.")
            content += f"\n**Event players:** {len(selected_ids(self.draft.get('members', '')))} · **Invited:** {len(selected_ids(self.draft.get('invited', '')))}"
            content += (f"\n**Duration:** {self.draft.get('durationMinutes', 120)} min"
                f" · **Auto-enroll:** {'On' if self.draft.get('autoEnroll') else 'Off'}"
                f" · **Event chat:** {'On' if self.draft.get('chatEnabled', True) else 'Off'}")
            if self.legacy_schedule and not self.draft.get("convertLegacySchedule"):
                content += "\nImported schedule and existing end condition retained."
            if self.draft.get("convertLegacySchedule"):
                content += "\n⚠️ This save replaces the imported schedule and its existing end condition."
            content += "\n\nNothing has been saved yet. Confirm below, or edit players and event settings."
            await interaction.edit_original_response(content=content, view=EventPreviewView(self), allowed_mentions=discord.AllowedMentions.none())
        except (ValueError, RuntimeError) as error:
            self.reviewed = None
            await interaction.edit_original_response(content=self.content() + "\n\n" + safe(error, 700) +
                "\nEdit the draft, then preview again before confirming.", view=EventPreviewView(self),
                allowed_mentions=discord.AllowedMentions.none())

    async def save(self, interaction):
        if not await self.interaction_check(interaction):
            return
        if self.reviewed != json.dumps(self.draft, sort_keys=True):
            return await interaction.response.send_message("Preview the current draft before confirming.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        async with self.saving:
            if self.submitted:
                return await interaction.followup.send("This draft has already been saved. Open /lfg manage.", ephemeral=True)
            if self.reviewed != json.dumps(self.draft, sort_keys=True):
                return await interaction.followup.send("The draft changed. Preview it again.", ephemeral=True)
            try:
                result = await api(interaction, "update" if self.event_id else "create", self.payload())
                self.submitted = True
                event = result["event"]
                try:
                    await api(interaction, "reconcile", {"eventId": event["id"]})
                    message = "Event saved and its persistent area synchronized."
                except Exception:
                    message = "Event saved. Discord synchronization is pending; check Skill health before retrying."
                await interaction.edit_original_response(content=message, view=None)
                self.stop()
            except (ValueError, RuntimeError) as error:
                await interaction.followup.send(str(error)[:1500], ephemeral=True)
