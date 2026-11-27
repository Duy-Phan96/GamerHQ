"""Discord interaction adapter for the installed Event Manager's public API.

No Event persistence, recurrence or participant policy is implemented here.
Host authorization and rendering wrap the Skill-owned management operations.
"""
from __future__ import annotations

import re
import logging

from database import db
from services.tenant_routing import add_shared_entry_view
from hosts.gamerhq.skill_resources import event_channel_identity
from skill_runtime import ManagementConflictError
from cogs.event_navigation import EventPicker, EventActions, ConfirmEventChange, session_open
from cogs.event_attendance import AttendanceView, InvitationView, LeaveMembershipView

import discord
from discord import app_commands
from discord.ext import commands, tasks
from services.tenant_routing import tenant_cycle
from database import tenants
from services import public_event_cards

from cogs.event_editor import SkillEventEditor, safe

SKILL = "event-manager"


def can_manage(member, event):
    rights = member.guild_permissions
    return bool(rights.administrator or rights.manage_guild or str(member.id) == event.get("ownerId"))


def can_view(member, event):
    return can_manage(member, event) or event["config"]["visibility"] == "public" or str(member.id) in event["members"]


def supports_attendance(interaction):
    registry = interaction.client.skill_runtime.registry
    return registry.contains(SKILL) and any(api.id == "event-manager.attendance-plan.v1"
        for api in registry.get(SKILL).manifest.management_apis.exposes)


async def notify_invited(guild, users):
    for user in sorted(users)[:25]:
        member = guild.get_member(int(user))
        if member is not None:
            try:
                await member.send(f"You have an event invitation in **{safe(guild.name, 100)}**. Open /lfg invitations there.",
                                  allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                continue


async def require_event_ready(interaction, *, public=False):
    """Guard member writes using the guild's actual lifecycle and Host policy."""
    runtime = interaction.client.skill_runtime
    status = await runtime.status(guild_id=interaction.guild.id, skill_id=SKILL)
    # A freshly installed Skill may become configured on its first event write.
    if not (status.installed and status.enabled and status.running):
        raise ValueError("Event Manager is not running for this server. Ask an admin to enable it under Server Management → Skills.")
    if status.health != "PASS":
        raise ValueError("Event Manager is not ready. Ask an admin to review Skill health.")
    if public:
        import json
        try:
            raw = db.get_setting(f"skill_resources:{interaction.guild.id}:{SKILL}")
            policy = json.loads(raw) if raw else {}
            allowed = isinstance(policy, dict) and policy.get("allowPublic") is True
        except (TypeError, ValueError):
            allowed = False
        if not allowed:
            raise ValueError("Public events are not authorized. Ask the server owner to review Public Events in Skill settings.")


async def call(interaction, operation, payload, *, after_member_change=False):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        raise PermissionError("Use Event Manager as a member of this server.")
    runtime = interaction.client.skill_runtime
    if not runtime.registry.contains(SKILL):
        raise ValueError("The reviewed Event Manager package is unavailable in this Host.")
    async def dispatch(op, data):
        return await runtime.call_management(guild_id=interaction.guild.id, skill_id=SKILL,
            contract_id=f"event-manager.{op}.v1", payload=data, actor_id=interaction.user.id)

    if operation in {"update", "delete", "delete-preview", "set-active", "transition", "occurrences-update", "reconcile"}:
        event_id = payload["occurrenceId"].rsplit(":", 1)[0] if operation == "occurrences-update" else payload["eventId"]
        current = (await dispatch("get", {"eventId": event_id}))["state"]
        if not can_manage(interaction.user, current) and not (operation == "reconcile" and after_member_change):
            raise PermissionError("You don't have permission to manage this event.")
    if operation in {"create", "validate", "update", "reconcile"}:
        # Validation and the final write both recheck readiness. A public
        # reconciliation uses the saved event visibility, never caller input.
        public = (current["config"]["visibility"] == "public" if operation == "reconcile"
                  else payload.get("visibility") == "public")
        await require_event_ready(interaction, public=public)
    if operation == "attendance-plan":
        current = (await dispatch("get", {"eventId": payload["eventId"]}))["state"]
        payload = {**payload, "manager": can_manage(interaction.user, current)}
    if payload.get("convertLegacySchedule") is True and not interaction.user.guild_permissions.administrator:
        raise PermissionError("Only an administrator can replace an imported schedule.")
    result = await dispatch(operation, payload)
    if operation in {"attendance-plan", "attendance-save", "invitations", "invite-response", "leave-membership"} and "error" in result:
        error = {"conflict": ManagementConflictError, "forbidden": PermissionError}.get(result["code"], ValueError)
        raise error(result["error"])
    return result


async def open_editor(interaction, event_id=None, *, players=False, convert=False):
    if event_id:
        result = await call(interaction, "get", {"eventId": event_id})
        if not can_manage(interaction.user, result["state"]):
            raise PermissionError("Only the event owner or a server administrator can edit this event.")
        draft, revision = dict(result["event"]), result["event"]["revision"]
        draft.pop("id", None)
        draft.pop("revision", None)
        if convert:
            draft["convertLegacySchedule"] = True
    else:
        result = await call(interaction, "describe", {})
        draft, revision = dict(result["defaultDraft"]), None
        from services.lfg_service import SERVER_TZ
        draft["timezone"] = str(SERVER_TZ)
        draft["members"] = str(interaction.user.id)
        title = db.get_setting(f"event_manager_default_title:{interaction.guild.id}")
        draft["name"] = title.strip() if isinstance(title, str) and 0 < len(title.strip()) <= 100 else "GamerHQ Event"
        if "remindersEnabled" in draft:
            reminder = db.get_setting(f"event_manager_default_reminder:{interaction.guild.id}")
            if reminder not in ("off", "5", "15", "30", "45"):
                reminder = "15"
            draft["remindersEnabled"] = reminder != "off"
            if reminder != "off":
                draft["voiceLeadMinutes"] = int(reminder)
    schema = interaction.client.skill_runtime.management_ui_schema(SKILL)
    collection = next(field.collection for section in schema.sections for field in section.fields if field.collection)
    view = SkillEventEditor(interaction.user.id, collection.item_fields, draft, event_id=event_id, revision=revision,
        guild_id=interaction.guild.id, legacy_schedule=bool(event_id and result["state"].get("legacyRecurrence")),
        owner_id=result["state"].get("ownerId") if event_id else None)
    if players:
        from cogs.event_editor import ParticipantsView
        view = ParticipantsView(view)
    await interaction.followup.send(content=view.content(), view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


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


class EventBrowseEntry(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Browse Public Events", style=discord.ButtonStyle.primary,
                       custom_id="gamerhq:events:browse-public")
    async def browse(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("EventManager")
        if cog is None:
            return await interaction.followup.send("Public event discovery is unavailable on this server.", ephemeral=True)
        try:
            await cog.navigate(interaction, "browse-public", ignore_channel=True)
        except (ValueError, PermissionError, RuntimeError) as error:
            await interaction.followup.send(str(error)[:300], ephemeral=True)


class EventCreateEntry(EventSkillHubView):
    @discord.ui.button(label="My Events", style=discord.ButtonStyle.secondary,
                       custom_id="gamerhq:events:my-events")
    async def my_events(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("EventManager")
        if cog is None:
            return await interaction.followup.send("Event Manager is unavailable on this server.", ephemeral=True)
        try:
            await cog.navigate(interaction, "manage", ignore_channel=True)
        except (ValueError, PermissionError, RuntimeError) as error:
            await interaction.followup.send(str(error)[:300], ephemeral=True)


class EventManager(commands.Cog):
    lfg = app_commands.Group(name="lfg", description="Manage events through the installed Event Manager Skill.")
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.public_cards.start()

    def cog_unload(self):
        self.public_cards.cancel()

    @tasks.loop(seconds=30)
    @tenant_cycle
    async def public_cards(self):
        binding = tenants.current()
        for guild in self.bot.guilds:
            if binding is not None and binding.guild_id != guild.id:
                continue
            try:
                await public_event_cards.sync(self.bot.skill_runtime, guild)
            except Exception as error:
                logging.getLogger(__name__).warning("Public LFG synchronization pending guild=%s error=%s",
                    guild.id, type(error).__name__)

    @public_cards.before_loop
    async def before_public_cards(self):
        await self.bot.wait_until_ready()

    @lfg.command(name="create", description="Create a one-time or recurring event.")
    async def create(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await open_editor(interaction)

    async def navigate(self, interaction, action, event_id=None, *, ignore_channel=False, server=False):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise PermissionError("Open Event Manager in your server.")
        if not ignore_channel:
            event_id = event_id or event_channel_identity(interaction.channel, guild_id=interaction.guild.id)
        if event_id:
            return await self.action(interaction, event_id, action)
        result = await call(interaction, "list", {})
        manageable = action not in ("join", "leave", "attendance-plan", "manage")
        admin = interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild
        if server and not admin:
            raise PermissionError("You don't have permission to list server events.")
        actor = str(interaction.user.id)
        participant_ids = {row["eventId"] for row in result.get("occurrences", ())
                           if row["status"] in ("scheduled", "active") and str(interaction.user.id) in row["participants"]}
        events = [event for event in result["events"]
                  if (can_manage(interaction.user, event) if manageable else
                      can_view(interaction.user, event) or (event["id"] in participant_ids and
                      actor not in event.get("revokedAccess", ()) and action != "attendance-plan"))]
        if action == "manage" and not server and (ignore_channel or not admin):
            events = [event for event in events if event.get("ownerId") == actor or actor in event["members"]
                      or event["id"] in participant_ids]
        if action == "browse-public":
            # Do not include private events even for owners, invitees or admins.
            events = [event for event in result["events"] if event["status"] == "active"
                      and event["config"]["visibility"] == "public"
                      and any(row["eventId"] == event["id"] and session_open(row, event)
                              for row in result.get("occurrences", ()))]
        if action == "convert":
            events = [event for event in events if event.get("legacyRecurrence")]
        if not events:
            return await interaction.followup.send("No upcoming public events." if action == "browse-public" else "No events available.", ephemeral=True)
        counts = {}
        for row in sorted(result.get("occurrences", ()), key=lambda item: item["startAt"]):
            if row["status"] in ("scheduled", "active"):
                counts.setdefault(row["eventId"], len(row["participants"]))
        title = ("All Events" if admin and (server or not ignore_channel) else "My Events") if action == "manage" or manageable else "Choose Event"
        if action == "browse-public":
            title = "Public Events"
        picker = EventPicker(self, interaction, events, counts, action, title)
        await interaction.followup.send(content=picker.content(), view=picker, ephemeral=True)

    async def action(self, interaction, event_id, action):
        if action in ("join", "leave"):
            return await self.member_action(interaction, event_id, action)
        if action in ("switch-event", "server-events"):
            return await self.navigate(interaction, "manage", ignore_channel=True, server=action == "server-events")
        if action == "attendance-plan":
            plan = await call(interaction, "attendance-plan", {"eventId": event_id, "days": 28})
            view = AttendanceView(self, interaction, event_id, plan)
            return await interaction.followup.send(view.content(), view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        result = await call(interaction, "get", {"eventId": event_id})
        state = result["state"]
        if action == "browse-public":
            if state["status"] != "active" or state["config"]["visibility"] != "public":
                raise ValueError("This event is no longer publicly available. Refresh the event list.")
            action = "manage"
        if action == "leave-series":
            actor = str(interaction.user.id)
            if actor not in state["members"] or actor == state.get("ownerId"):
                raise PermissionError("Only a current member can leave their membership.")
            view = LeaveMembershipView(self, interaction, event_id, state)
            return await interaction.followup.send(view.content(), view=view, ephemeral=True)
        if action == "manage":
            viewable = can_view(interaction.user, state) or any(
                row["status"] in ("active", "scheduled") and str(interaction.user.id) in row["participants"]
                and str(interaction.user.id) not in state.get("revokedAccess", ())
                for row in result.get("history", {}).get("occurrences", ()))
            if not viewable:
                raise PermissionError("You don't have access to this event.")
            manages = can_manage(interaction.user, state)
            available = hasattr(interaction, "client") and supports_attendance(interaction)
            view = EventActions(self, interaction, state, result.get("history", {}).get("occurrences", ()), manages,
                                attendance=available)
            return await interaction.followup.send(content=view.content(), view=view, ephemeral=True)
        if not can_manage(interaction.user, state):
            raise PermissionError("You don't have permission to manage this event.")
        if state["status"] == "deleted":
            raise ValueError("This event has ended.")
        if action == "sync":
            await call(interaction, "reconcile", {"eventId": event_id})
            return await interaction.followup.send(
                "Event Discord synchronization completed for the existing event.", ephemeral=True)
        if action in ("edit", "players", "convert"):
            if action == "convert" and (not interaction.user.guild_permissions.administrator or not state.get("legacyRecurrence")):
                raise PermissionError("This imported schedule requires administrator access.")
            return await open_editor(interaction, event_id, players=action == "players", convert=action == "convert")
        if action in ("delete", "cancel-session"):
            if action == "delete":
                proof = await call(interaction, "delete-preview", {"eventId": event_id})
                view = ConfirmEventChange(self, interaction, event_id, "delete", proof["revision"])
                message = ("Delete this event? Future sessions will stop. The event chat, messages and recorded attendance "
                           "will be kept. Players will no longer be able to join.")
            else:
                rows = [row for row in result.get("history", {}).get("occurrences", ()) if session_open(row, state)]
                if not rows:
                    raise ValueError("There is no session to cancel.")
                row = min(rows, key=lambda item: item["startAt"])
                view = ConfirmEventChange(self, interaction, event_id, "transition", row["revision"], row["startAt"])
                message = f"Cancel the session on <t:{row['startAt']}:F>? The event, future sessions and attendance history will be kept."
            return await interaction.followup.send(message, view=view, ephemeral=True)
        if action in ("pause", "resume"):
            await call(interaction, "set-active", {"eventId": event_id, "revision": state["revision"], "active": action == "resume"})
            return await self.finish(interaction, event_id, "Event resumed." if action == "resume" else "Event paused. Its chat and history are kept.")
        raise ValueError("Choose an event action.")

    async def finish(self, interaction, event_id, message, *, after_member_change=False):
        try:
            # Trusted continuation only after the Skill accepted the member mutation.
            await call(interaction, "reconcile", {"eventId": event_id}, after_member_change=after_member_change)
        except Exception:
            logging.getLogger(__name__).warning("Event Discord reconciliation is pending", exc_info=True)
            message += " The Discord card will refresh when synchronization resumes."
        await interaction.followup.send(message, ephemeral=True)

    @lfg.command(name="edit", description="Edit the current event or pick one from My Events.")
    async def edit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.navigate(interaction, "edit")

    @lfg.command(name="manage", description="Open the current event or your event list.")
    async def manage(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.navigate(interaction, "manage")

    @lfg.command(name="attendance", description="Plan attendance for upcoming sessions.")
    async def attendance(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not supports_attendance(interaction):
            return await interaction.followup.send("Attendance planning is unavailable on this server.", ephemeral=True)
        await self.navigate(interaction, "attendance-plan")

    @lfg.command(name="invitations", description="Review your pending event invitations in this server.")
    async def invitations(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not supports_attendance(interaction):
            return await interaction.followup.send("Event invitations are unavailable on this server.", ephemeral=True)
        rows = (await call(interaction, "invitations", {}))["invitations"]
        if not rows:
            return await interaction.followup.send("You have no pending invitations.", ephemeral=True)
        view = InvitationView(self, interaction, rows)
        await interaction.followup.send(view.content(), view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @lfg.command(name="default-reminder", description="Set the default reminder for new events.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def default_reminder(self, interaction: discord.Interaction, minutes: str):
        if not interaction.guild or minutes not in ("off", "5", "15", "30", "45"):
            raise ValueError("Choose off, 5, 15, 30, or 45 minutes.")
        db.set_setting(f"event_manager_default_reminder:{interaction.guild.id}", minutes)
        await interaction.response.send_message("Default event reminder saved.", ephemeral=True)

    async def member_action(self, interaction, event_id, action, *, start_at=None):
        manifest = interaction.client.skill_runtime.registry.get(SKILL).manifest
        if any(contract.id == "event-manager.attendance.v1" for contract in manifest.management_apis.exposes):
            result = await call(interaction, "attendance", {"eventId": event_id, "action": action, "startAt": start_at})
            return await self.finish(interaction, event_id, result["message"], after_member_change=True)
        # Existing 0.1.0 hosts can stage the adapter ahead of the Skill update.
        if action == "leave-series":
            raise ValueError("Permanent membership can be changed in Manage Players.")
        detail = await call(interaction, "get", {"eventId": event_id})
        state = detail["state"]
        rows = (await call(interaction, "history", {"eventId": event_id}))["occurrences"]
        available = [row for row in rows if row["status"] in ("scheduled", "active") and
                     (start_at is None or row["startAt"] == start_at)]
        row = min(available, key=lambda item: item["startAt"], default=None)
        actor = str(interaction.user.id)
        members = set(row["participants"]) if row else set()
        if state["config"]["visibility"] != "public" and actor not in members | set(state["members"] + state["invited"]) | {state.get("ownerId")}:
            raise PermissionError("You don't have access to this event.")
        if state["status"] != "active":
            raise ValueError("This event is currently paused. Ask the host to resume it." if state["status"] == "paused" else "This event has ended.")
        if not row:
            raise ValueError("There is no upcoming session for this event.")
        if action == "join" and actor in members:
            return await interaction.followup.send("You're already registered for this session.", ephemeral=True)
        if action == "leave" and actor not in members:
            return await interaction.followup.send("You're not registered for this session.", ephemeral=True)
        if action == "join" and len(members) >= state["config"]["maxPlayers"]:
            raise ValueError("This session is full.")
        members = members | {actor} if action == "join" else members - {actor}
        await call(interaction, "participate", {"eventId": event_id, "startAt": row["startAt"],
            "members": sorted(members), "revision": row["revision"]})
        await self.finish(interaction, event_id, "You joined the next session." if action == "join" else "You left the next session.", after_member_change=True)

    @lfg.command(name="join", description="Join the next session of the current or selected event.")
    async def join(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.navigate(interaction, "join")

    @lfg.command(name="leave", description="Leave the next session of the current or selected event.")
    async def leave(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.navigate(interaction, "leave")

    @lfg.command(name="participants", description="Manage permanent players and invitations.")
    async def participants(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.navigate(interaction, "players")

    @lfg.command(name="join-code", description="Join a migrated private occurrence using its existing invitation code.")
    async def join_code(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        await call(interaction, "join-code", {"code": code.strip()})
        await interaction.followup.send("Participation saved. Discord access will be reconciled.", ephemeral=True)

    @lfg.command(name="active", description="Open event controls including Pause and Resume.")
    async def active(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.navigate(interaction, "manage")

    @lfg.command(name="convert-schedule", description="Administrator review of an imported recurring schedule.")
    @app_commands.checks.has_permissions(administrator=True)
    async def convert_schedule(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.navigate(interaction, "convert")

    @lfg.command(name="default-title", description="Set the default title for new events on this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def default_title(self, interaction: discord.Interaction, title: str):
        title = title.strip()
        if not interaction.guild or not 1 <= len(title) <= 100:
            raise ValueError("Enter a title from 1 to 100 characters in your server.")
        db.set_setting(f"event_manager_default_title:{interaction.guild.id}", title)
        await interaction.response.send_message("Default event title saved.", ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        data = interaction.data or {}
        custom_id = str(data.get("custom_id", ""))
        public = re.fullmatch(r"gamerhq:lfg-card:(join|leave|details|share):([0-9a-f]{32})", custom_id)
        if public and not interaction.response.is_done() and interaction.guild and interaction.message:
            await interaction.response.defer(ephemeral=True)
            try:
                async with public_event_cards.guild_lock(interaction.guild.id):
                    identity = public_event_cards.resolve_control(interaction.guild, interaction.channel,
                        interaction.message, public[2])
                    if not identity:
                        raise ValueError("This public card is no longer available.")
                    await require_event_ready(interaction, public=True)
                    event_id, start_at = identity
                    cards = public_event_cards.project(await call(interaction, "list", {}))
                    if event_id not in cards or cards[event_id].start_at != start_at:
                        raise ValueError("This session has changed. Use the updated public event card.")
                    if public[1] in ("join", "leave"):
                        await self.member_action(interaction, event_id, public[1], start_at=start_at)
                    elif public[1] == "details":
                        await self.action(interaction, event_id, "browse-public")
                    else:
                        await interaction.followup.send(interaction.message.jump_url, ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none())
            except (ValueError, PermissionError, RuntimeError, ManagementConflictError):
                await interaction.followup.send("This public event is no longer available or the action could not be completed.",
                    ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        card = re.fullmatch(r"gamerhq:event:manage:[0-9a-f]{32}", custom_id)
        if card and not interaction.response.is_done() and interaction.guild and interaction.message:
            event_id = event_channel_identity(interaction.channel, guild_id=interaction.guild.id,
                                              message_id=interaction.message.id, control_id=custom_id)
            if not event_id or interaction.message.author.id != interaction.guild.me.id:
                return
            await interaction.response.defer(ephemeral=True)
            try:
                await self.action(interaction, event_id, "manage")
            except (ValueError, PermissionError, ManagementConflictError) as error:
                await interaction.followup.send(str(error)[:300], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
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
        error = getattr(error, "original", error)
        if isinstance(error, (ValueError, PermissionError, ManagementConflictError, app_commands.CheckFailure)):
            message = str(error)[:300]
        else:
            logging.getLogger(__name__).error("Event interaction failed", exc_info=error)
            message = "Event Manager could not complete this action. Try again or contact a server administrator."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot):
    add_shared_entry_view(bot, EventCreateEntry())
    add_shared_entry_view(bot, EventBrowseEntry())
    await bot.add_cog(EventManager(bot))
