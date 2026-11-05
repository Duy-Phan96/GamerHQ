"""Private Discord views for the Event Manager's attendance and consent contracts."""
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from cogs.event_editor import safe
from cogs.event_navigation import EventView
from skill_runtime import ManagementConflictError


class AttendanceView(EventView):
    def __init__(self, cog, interaction, event_id, plan):
        super().__init__(cog, interaction)
        self.event_id, self.plan = event_id, plan
        self.days, self.page, self.roster_page = 14, 0, 0
        self.selected = None
        self.draft = {}
        self.saving = False
        self.rebuild()

    async def interaction_check(self, interaction):
        if not await super().interaction_check(interaction):
            return False
        if self.saving:
            await interaction.response.send_message("Attendance is being saved.", ephemeral=True)
            return False
        return True

    def sessions(self):
        end = self.plan["windowStartAt"] + self.days * 86400
        return [row for row in self.plan["sessions"] if row["startAt"] <= end]

    def label(self, row):
        attending = self.draft.get(row["startAt"], row["attending"])
        if row["status"] != "scheduled":
            return row["status"].title()
        if not attending and len(row["participants"]) >= row["capacity"]:
            return "Full"
        return "Attending" if attending else "Not attending"

    def content(self):
        lines = [f"# My Attendance · {safe(self.plan['name'], 100)}", f"Next {self.days // 7} weeks · {len(self.draft)} unsaved changes"]
        if not self.sessions():
            lines.append("No sessions in this window.")
        if self.selected:
            row = self.selected
            roster = row["participants"]
            start = self.roster_page * 20
            names = ", ".join(f"<@{player}>" for player in roster[start:start + 20]) or "No registered players"
            lines.extend([f"<t:{row['startAt']}:F> · {self.label(row)}",
                f"{len(roster)}/{row['capacity']} registered · {max(0, row['capacity'] - len(roster))} places available",
                names])
            if len(roster) > 20:
                lines.append(f"Players page {self.roster_page + 1} of {(len(roster) + 19) // 20}")
        return "\n".join(lines)

    async def refresh(self, interaction):
        self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self, allowed_mentions=discord.AllowedMentions.none())

    def rebuild(self):
        self.clear_items()
        sessions = self.sessions()
        visible = sessions[self.page * 14:(self.page + 1) * 14]
        if visible:
            zone = ZoneInfo(self.plan["timezone"])
            select = discord.ui.Select(placeholder="Choose a session", row=0, options=[
                discord.SelectOption(label=datetime.fromtimestamp(row["startAt"], zone).strftime("%a %d %b · %H:%M"),
                    value=str(i), description=f"{self.label(row)} · {len(row['participants'])}/{row['capacity']} registered",
                    default=row == self.selected) for i, row in enumerate(visible)])

            async def select_session(interaction):
                self.selected, self.roster_page = visible[int(select.values[0])], 0
                await self.refresh(interaction)

            select.callback = select_session
            self.add_item(select)

        def button(label, row, callback, *, disabled=False, style=discord.ButtonStyle.secondary):
            item = discord.ui.Button(label=label, row=row, disabled=disabled, style=style)
            item.callback = callback
            self.add_item(item)

        async def window(interaction):
            self.days = 28 if self.days == 14 else 14
            self.page, self.selected, self.roster_page = 0, None, 0
            await self.refresh(interaction)

        async def previous(interaction):
            self.page -= 1
            await self.refresh(interaction)

        async def next_page(interaction):
            self.page += 1
            await self.refresh(interaction)

        button("Show 4 Weeks" if self.days == 14 else "Show 2 Weeks", 1, window)
        button("Previous Dates", 1, previous, disabled=self.page == 0)
        button("Next Dates", 1, next_page, disabled=len(sessions) <= (self.page + 1) * 14)
        if self.selected:
            row = self.selected
            attending = self.draft.get(row["startAt"], row["attending"])

            async def toggle(interaction):
                if attending != row["attending"]:
                    self.draft.pop(row["startAt"], None)
                else:
                    self.draft[row["startAt"]] = not attending
                await self.refresh(interaction)

            async def more(interaction):
                self.roster_page = (self.roster_page + 1) % ((len(row["participants"]) + 19) // 20)
                await self.refresh(interaction)

            if self.plan["canEdit"] and row["status"] == "scheduled":
                button("Leave This Session" if attending else "Attend This Session", 2, toggle,
                    disabled=not attending and not row["attending"] and
                    (self.label(row) == "Full" or self.plan["status"] == "paused"))
            if len(row["participants"]) > 20:
                button("More Players", 2, more)

        button("Save Attendance", 3, self.save, disabled=not self.draft, style=discord.ButtonStyle.success)
        button("Cancel", 3, self.cancel)

    async def save(self, interaction):
        from cogs.event_manager import call
        self.saving = True
        changes = [{"startAt": row["startAt"], "revision": row["revision"], "attending": self.draft[row["startAt"]]}
                   for row in self.plan["sessions"] if row["startAt"] in self.draft]
        try:
            await interaction.response.defer(ephemeral=True)
            await call(interaction, "attendance-save", {"eventId": self.event_id, "revision": self.plan["revision"], "changes": changes})
        except (ValueError, PermissionError, ManagementConflictError) as error:
            return await interaction.followup.send(str(error)[:300], ephemeral=True)
        finally:
            self.saving = False
        self.stop()
        await interaction.edit_original_response(content="Attendance saved.", view=None)
        await self.cog.finish(interaction, self.event_id, "Your permanent membership is unchanged.")

    async def cancel(self, interaction):
        self.stop()
        await interaction.response.edit_message(content="Attendance changes discarded.", view=None)


class InvitationView(EventView):
    def __init__(self, cog, interaction, invitations):
        super().__init__(cog, interaction)
        self.rows, self.page, self.selected = invitations, 0, None
        self.rebuild()

    def content(self):
        return f"# Event Invitations\n\n{safe(self.selected['name'], 100) if self.selected else 'Choose an invitation.'}"

    def rebuild(self):
        for item in tuple(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        rows = self.rows[self.page * 25:(self.page + 1) * 25]
        select = discord.ui.Select(placeholder="Choose an invitation", row=0,
            options=[discord.SelectOption(label=row["name"][:100], value=str(i)) for i, row in enumerate(rows)])

        async def choose(interaction):
            self.selected = rows[int(select.values[0])]
            self.rebuild()
            await interaction.response.edit_message(content=self.content(), view=self)

        select.callback = choose
        self.add_item(select)
        self.accept.disabled = self.decline.disabled = self.selected is None
        self.next_page.disabled = len(self.rows) <= 25

    async def respond(self, interaction, accept):
        from cogs.event_manager import call
        if self.selected is None:
            return
        await interaction.response.defer(ephemeral=True)
        row = self.selected
        try:
            result = await call(interaction, "invite-response", {"eventId": row["eventId"], "revision": row["revision"], "accept": accept})
        except (ValueError, PermissionError, ManagementConflictError) as error:
            return await interaction.followup.send(str(error)[:300], ephemeral=True)
        self.stop()
        await interaction.edit_original_response(content=f"Invitation {result['state']}.", view=None)
        if result["synchronizationPending"]:
            await self.cog.finish(interaction, row["eventId"], "You are now an event member.")

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, row=1)
    async def accept(self, interaction, button):
        await self.respond(interaction, True)

    @discord.ui.button(label="Decline", row=1)
    async def decline(self, interaction, button):
        await self.respond(interaction, False)

    @discord.ui.button(label="More Invitations", row=1)
    async def next_page(self, interaction, button):
        self.page = (self.page + 1) % ((len(self.rows) + 24) // 25)
        self.selected = None
        self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)


class LeaveMembershipView(EventView):
    def __init__(self, cog, interaction, event_id, state):
        super().__init__(cog, interaction)
        self.event_id, self.state = event_id, state

    def content(self):
        if self.state["config"]["visibility"] == "private":
            return "You will leave this event and lose access to its private chat. Your past attendance will remain in the event history."
        return "You will leave this event and stop automatic registration. Your past attendance will remain in the event history."

    @discord.ui.button(label="Leave Event Permanently", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        from cogs.event_manager import call, supports_attendance
        await interaction.response.defer(ephemeral=True)
        try:
            payload = {"eventId": self.event_id, "revision": self.state["revision"]}
            if supports_attendance(interaction):
                result = await call(interaction, "leave-membership", payload)
            else:
                result = await call(interaction, "attendance", {**payload, "action": "leave-series"})
        except (ValueError, PermissionError, ManagementConflictError) as error:
            return await interaction.followup.send(str(error)[:300], ephemeral=True)
        self.stop()
        await interaction.edit_original_response(content="Membership ended.", view=None)
        await self.cog.finish(interaction, self.event_id, result["message"])

    @discord.ui.button(label="Keep Membership")
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="Membership kept.", view=None)
