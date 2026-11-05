"""Transient Discord views for the installed Event Manager's public operations."""
from __future__ import annotations

from datetime import datetime
import logging
import time
from zoneinfo import ZoneInfo

import discord
from skill_runtime import ManagementConflictError

from cogs.event_editor import safe


def session_open(row, event):
    duration = row.get("configSnapshot", event["config"])["durationMinutes"]
    return row["status"] in ("scheduled", "active") and row["startAt"] + duration * 60 > time.time()


class EventView(discord.ui.View):
    def __init__(self, cog, interaction):
        super().__init__(timeout=600)
        self.cog = cog
        self.actor_id = interaction.user.id
        self.guild_id = interaction.guild.id

    async def interaction_check(self, interaction):
        if getattr(interaction.guild, "id", None) == self.guild_id and interaction.user.id == self.actor_id:
            return True
        await interaction.response.send_message("Open your own event controls to use this action.", ephemeral=True)
        return False

    async def invoke(self, interaction, event_id, action):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.action(interaction, event_id, action)
        except (ValueError, PermissionError, ManagementConflictError) as error:
            await interaction.followup.send(str(error)[:300], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def on_error(self, interaction, error, item):
        logging.getLogger(__name__).error("Event navigation failed", exc_info=error)
        response = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await response("Event Manager could not complete this action. Try again.", ephemeral=True)


class EventPicker(EventView):
    PAGE_SIZE = 25

    def __init__(self, cog, interaction, events, counts, action, title):
        super().__init__(cog, interaction)
        self.events = sorted(events, key=lambda event: (event.get("nextOccurrence") or float("inf"), event["config"]["name"].casefold()))
        self.action, self.title, self.counts = action, title, counts
        self.page = 0
        self.rebuild()

    def content(self):
        pages = (len(self.events) - 1) // self.PAGE_SIZE + 1
        return f"# {self.title}\n\nChoose an event · Page {self.page + 1} of {pages}"

    def rebuild(self):
        self.clear_items()
        start = self.page * self.PAGE_SIZE
        events = self.events[start:start + self.PAGE_SIZE]
        options = []
        for i, event in enumerate(events):
            config = event["config"]
            stamp = event.get("nextOccurrence")
            date = datetime.fromtimestamp(stamp, ZoneInfo(config["timezone"])).strftime("%d %b %H:%M") if stamp else "No upcoming date"
            parts = [event["status"].title(), date, f"{len(event['members'])} members",
                     f"{self.counts.get(event['id'], 0)}/{config['maxPlayers']} registered"]
            if config.get("game"):
                parts.insert(0, config["game"])
            options.append(discord.SelectOption(label=config["name"][:100], value=str(i), description=" · ".join(parts)[:100]))
        select = discord.ui.Select(placeholder="Choose by event title", options=options)

        async def selected(interaction):
            await self.invoke(interaction, events[int(select.values[0])]["id"], self.action)

        select.callback = selected
        self.add_item(select)
        for shift, label, disabled in ((-1, "Previous", self.page == 0), (1, "Next", start + self.PAGE_SIZE >= len(self.events))):
            button = discord.ui.Button(label=label, row=1, disabled=disabled)

            async def turn(interaction, delta=shift):
                if await self.interaction_check(interaction):
                    self.page = min(max(self.page + delta, 0), (len(self.events) - 1) // self.PAGE_SIZE)
                    self.rebuild()
                    await interaction.response.edit_message(content=self.content(), view=self)

            button.callback = turn
            self.add_item(button)


class EventActions(EventView):
    def __init__(self, cog, interaction, event, occurrences, manages):
        super().__init__(cog, interaction)
        self.event = event
        self.session = min((row for row in occurrences if session_open(row, event)),
                           key=lambda row: row["startAt"], default=None)
        if event["status"] == "deleted":
            return
        user = str(interaction.user.id)
        attending = self.session and user in self.session["participants"]
        actions = []
        if attending:
            actions.append(("leave", "Leave"))
        elif event["status"] == "active" and (event["config"]["visibility"] == "public"
                or user in event["members"] + event["invited"] or user == event.get("ownerId")):
            actions.append(("join", "Join"))
        if user in event["members"] and user != event.get("ownerId"):
            actions.append(("leave-series", "Leave Event"))
        if manages:
            actions.extend([("edit", "Edit Event"), ("players", "Manage Players"),
                            ("pause", "Pause") if event["status"] == "active" else ("resume", "Resume")])
            if self.session:
                actions.append(("cancel-session", "Cancel Session"))
            actions.append(("delete", "Delete Event"))
        for index, (action, label) in enumerate(actions):
            button = discord.ui.Button(label=label, row=index // 4,
                style=discord.ButtonStyle.danger if action in ("delete", "cancel-session") else discord.ButtonStyle.secondary)

            async def clicked(interaction, choice=action):
                await self.invoke(interaction, event["id"], choice)

            button.callback = clicked
            self.add_item(button)

    def content(self):
        event, config = self.event, self.event["config"]
        lines = [f"# {safe(config['name'], 100)}", f"{event['status'].title()} · {config['visibility'].title()}"]
        if config.get("game"):
            lines.append(f"Game: {safe(config['game'], 100)}")
        if event.get("summary"):
            lines.append(safe(event["summary"], 300))
        lines.append(f"{len(event['members'])} event members")
        if self.session:
            lines.append(f"<t:{self.session['startAt']}:F> · {len(self.session['participants'])}/{config['maxPlayers']} registered")
        elif event.get("nextOccurrence"):
            lines.append(f"Next session: <t:{event['nextOccurrence']}:F>")
        return "\n".join(lines)


class ConfirmEventChange(EventView):
    def __init__(self, cog, interaction, event_id, operation, revision, start_at=None):
        super().__init__(cog, interaction)
        self.operation, self.event_id, self.revision, self.start_at = operation, event_id, revision, start_at
        self.confirm.label = "Delete Event" if operation == "delete" else "Cancel Session"

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        from cogs.event_manager import call
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        payload = {"eventId": self.event_id, "revision": self.revision}
        if self.start_at is not None:
            payload.update(startAt=self.start_at, status="cancelled")
        try:
            await call(interaction, self.operation, payload)
        except (ValueError, PermissionError, ManagementConflictError) as error:
            return await interaction.followup.send(str(error)[:300], ephemeral=True)
        self.stop()
        await interaction.edit_original_response(content="Event ended." if self.operation == "delete" else "Session cancelled.", view=None)
        await self.cog.finish(interaction, self.event_id, "The event chat and attendance history were retained.")

    @discord.ui.button(label="Keep Event", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if await self.interaction_check(interaction):
            self.stop()
            await interaction.response.edit_message(content="No changes saved.", view=None)
