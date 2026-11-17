"""Guild admission and scoped ingress for a reviewed multi-guild cohort."""
import asyncio
from contextlib import ExitStack, contextmanager
from contextvars import Context, ContextVar
from functools import wraps
import logging
import re

import discord
from discord import app_commands
from discord.http import Route
from discord.state import ConnectionState
from discord.ui.view import ViewStore
from database import tenants


class GuildAdmissionError(PermissionError):
    """No current proof authorizes the operation on this guild."""


_interaction_actor = ContextVar("gamerhq_interaction_actor", default=None)
_GUILD_EVENTS = frozenset({
    "on_interaction", "on_message", "on_raw_message_edit", "on_raw_message_delete",
    "on_raw_bulk_message_delete", "on_guild_channel_create", "on_guild_channel_update",
    "on_guild_channel_delete", "on_guild_role_create", "on_guild_role_update",
    "on_guild_role_delete", "on_guild_update", "on_member_join", "on_member_remove",
    "on_member_update", "on_voice_state_update",
})
MEMBERSHIP_EVENTS = frozenset({"guild_join", "guild_remove", "disconnect", "resumed"})


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def event_guild(event_name, args):
    if event_name not in _GUILD_EVENTS:
        return None
    ids = set()
    for item in args:
        guild = item if isinstance(item, discord.Guild) else getattr(item, "guild", None)
        ids.update(ident for ident in (getattr(guild, "id", None), getattr(item, "guild_id", None))
                   if type(ident) is int)
    return next(iter(ids)) if len(ids) == 1 else None


def verified_actor(guild, user):
    """Accept the Discord envelope member only within its verified ingress."""
    binding = tenants.current()
    if binding is not None and binding.guild_id == guild.id and _interaction_actor.get() is user:
        return user
    return None


def _discord_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise GuildAdmissionError("Invalid Discord identity in membership evidence.")
    return int(value)


def verify_membership(application_id, approved, bot_user, guilds):
    if (not isinstance(bot_user, dict) or bot_user.get("bot") is not True
            or _discord_id(bot_user.get("id")) != application_id):
        raise GuildAdmissionError("Discord application identity could not be verified.")
    if not isinstance(guilds, list) or len(guilds) != len(approved):
        raise GuildAdmissionError("Discord memberships differ from the approved guilds.")
    observed = {_discord_id(row.get("id") if isinstance(row, dict) else None) for row in guilds}
    if observed != approved:
        raise GuildAdmissionError("Discord memberships differ from the approved guilds.")


class GuildRoutes:
    """Starts disabled. Only the trusted Host can submit Discord membership evidence."""

    def __init__(self, *, application_id, store, get_guild):
        self.application_id = _discord_id(str(application_id))
        self.store = store
        self.get_guild = get_guild
        self.allowed = frozenset({store.primary_guild, *store.additional_guilds})
        if len(self.allowed) != 2:
            raise ValueError("The pilot requires exactly two approved guilds.")
        self._proof = None
        self._revision = 0
        self._tasks = {}

    async def refresh(self, http):
        self.invalidate()
        revision = self._revision
        identity = await http.request(Route("GET", "/users/@me"))
        guilds = await http.request(Route("GET", "/users/@me/guilds"), params={"limit": 200})
        if revision != self._revision:
            raise GuildAdmissionError("Membership changed during verification.")
        self.verify(identity, guilds)

    def verify(self, bot_user, guilds):
        """Check the exact set from GET /users/@me/guilds?limit=200 and the bot identity."""
        self.invalidate()
        verify_membership(self.application_id, self.allowed, bot_user, guilds)
        self._proof = object()

    def invalidate(self):
        self._proof = None
        self._revision += 1
        current_task = _task()
        for task in tuple(self._tasks):
            if task is not current_task:
                task.cancel()

    def require_runtime_scope(self, guild_id):
        bound = tenants.current()
        if bound is None:
            raise GuildAdmissionError("Pilot guild operations require a verified tenant scope.")
        tenants.require_guild(guild_id)
        self._guild(guild_id)

    def _guild(self, guild_id):
        if self._proof is None or type(guild_id) is not int or guild_id not in self.allowed:
            raise GuildAdmissionError("Guild has no verified admission.")
        guild = self.get_guild(guild_id)
        if (guild is None or guild.id != guild_id or getattr(guild, "unavailable", False)
                or getattr(getattr(guild, "me", None), "id", None) != self.application_id):
            raise GuildAdmissionError("Guild membership could not be verified.")
        return guild

    @contextmanager
    def scope(self, guild_id):
        self._guild(guild_id)
        proof = self._proof

        def validate():
            if self._proof is not proof:
                raise GuildAdmissionError("Guild membership evidence expired.")
            self._guild(guild_id)

        with ExitStack() as stack:
            try:
                binding = stack.enter_context(self.store.scope(guild_id, validate=validate))
            except PermissionError as error:
                raise GuildAdmissionError("Guild storage could not be verified.") from error
            task = _task()
            if task is not None:
                self._tasks[task] = self._tasks.get(task, 0) + 1
            try:
                yield binding
            finally:
                if task is not None:
                    remaining = self._tasks[task] - 1
                    if remaining:
                        self._tasks[task] = remaining
                    else:
                        self._tasks.pop(task)

    @contextmanager
    def interaction(self, interaction):
        guild_id = interaction.guild_id
        guild = self._guild(guild_id)
        actor = interaction.user
        if (interaction.application_id != self.application_id or interaction.guild is not guild
                or not isinstance(actor, discord.Member) or actor.guild is not guild):
            raise GuildAdmissionError("Interaction does not belong to this guild.")
        with self.scope(guild_id) as binding:
            token = _interaction_actor.set(actor)
            try:
                yield binding
            finally:
                _interaction_actor.reset(token)

    async def restore(self, runtime, guild_id):
        with self.scope(guild_id):
            return await runtime.restore_guild(guild_id=guild_id)

    async def run_due(self, runtime, guild_id, **kwargs):
        with self.scope(guild_id):
            return await runtime.scheduler.run_due(**kwargs)

    async def sync_commands(self, tree):
        """Register only in the admitted guilds, with no global command writes."""
        for guild_id in sorted(self.allowed):
            guild = discord.Object(id=guild_id)
            with self.scope(guild_id):
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)

    async def serve_scheduler(self, runtime, stop, poll_seconds):
        while not stop.is_set():
            results = await asyncio.gather(*(self.run_due(runtime, guild_id) for guild_id in sorted(self.allowed)),
                                           return_exceptions=True)
            for guild_id, result in zip(sorted(self.allowed), results, strict=True):
                if isinstance(result, Exception) and not isinstance(result, GuildAdmissionError):
                    logging.getLogger(__name__).error("Tenant scheduler pass failed guild=%s error=%s",
                                                      guild_id, type(result).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass

    def create_retry(self, guild_id, callback):
        self.require_runtime_scope(guild_id)
        proof = self._proof

        async def run():
            if self._proof is proof:
                with self.scope(guild_id):
                    await callback()

        return asyncio.create_task(run(), context=Context())


def tenant_cycle(callback):
    """Run each existing cog polling pass in one admitted guild at a time."""
    @wraps(callback)
    async def run(cog):
        routes = getattr(cog.bot, "guild_routes", None)
        if routes is None:
            return await callback(cog)

        async def tick(guild_id):
            with routes.scope(guild_id):
                await callback(cog)

        results = await asyncio.gather(*(tick(guild_id) for guild_id in sorted(routes.allowed)),
                                       return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, GuildAdmissionError):
                logging.getLogger(__name__).error("Tenant cog pass failed error=%s", type(result).__name__)

    return run


async def route_interaction(interaction, callback, *args):
    """Bind the Discord envelope before existing command or view authorization."""
    routes = getattr(interaction.client, "guild_routes", None)
    if routes is None:
        return await callback(*args)
    with ExitStack() as stack:
        try:
            stack.enter_context(routes.interaction(interaction))
        except GuildAdmissionError:
            text = "GamerHQ is unavailable for this server. Contact its owner."
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            return None
        return await callback(*args)


class GuildCommandTree(app_commands.CommandTree):
    async def _call(self, interaction):
        # discord.py's tree invokes its checks and command in this awaited call.
        return await route_interaction(interaction, super()._call, interaction)

class GuildViewStore(ViewStore):
    """Scope dispatch of ordinary and persistent views, modals, and dynamic items."""

    def add_view(self, view, message_id=None):
        # discord.py uses the same scheduled-task entry point for raw Views,
        # LayoutViews and Modals, with different positional argument shapes.
        if not getattr(view, "_gamerhq_scoped", False):
            original = view._scheduled_task
            tenant = tenants.current()
            origin = tenant.guild_id if tenant else None
            modal = view.__discord_ui_modal__

            async def run(*args):
                interaction = args[0 if modal else 1]
                routes = getattr(interaction.client, "guild_routes", None)
                if routes is not None:
                    expected = origin if origin is not None else routes.store.primary_guild
                    if interaction.guild_id != expected:
                        return
                return await route_interaction(interaction, original, *args)

            view._scheduled_task = run
            view._gamerhq_scoped = True
        return super().add_view(view, message_id)

    async def schedule_dynamic_item_call(self, component_type, factory, interaction, custom_id, match):
        return await route_interaction(interaction, super().schedule_dynamic_item_call, component_type,
                                       factory, interaction, custom_id, match)


class GuildConnectionState(ConnectionState):
    def clear(self, *, views=True):
        super().clear(views=views)
        if views:
            self._view_store = GuildViewStore(self)

    def parse_interaction_create(self, data):
        routes = getattr(self._get_client(), "guild_routes", None)
        if routes is not None:
            try:
                if _discord_id(data.get("application_id")) != routes.application_id:
                    return
                routes._guild(_discord_id(data.get("guild_id")))
            except GuildAdmissionError:
                return
        return super().parse_interaction_create(data)
