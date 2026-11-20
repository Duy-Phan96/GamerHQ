"""Guild admission and scoped ingress for a reviewed multi-guild cohort."""
import asyncio
from contextlib import ExitStack, contextmanager
from contextvars import Context, ContextVar
from functools import wraps
import logging
import json
import re

import discord
from discord import app_commands
from discord.http import Route
from discord.state import ConnectionState
from discord.ui.view import ViewStore
from database import tenants


class GuildAdmissionError(PermissionError):
    """No current proof authorizes the operation on this guild."""


class GuildCorePermissionsError(GuildAdmissionError):
    def __init__(self, guild_id, missing, message):
        super().__init__(message)
        self.guild_id, self.missing = guild_id, missing


_interaction_actor = ContextVar("gamerhq_interaction_actor", default=None)
_membership_read = ContextVar("gamerhq_membership_read", default=None)
_GUILD_EVENTS = frozenset({
    "on_interaction", "on_message", "on_raw_message_edit", "on_raw_message_delete",
    "on_raw_bulk_message_delete", "on_guild_channel_create", "on_guild_channel_update",
    "on_guild_channel_delete", "on_guild_role_create", "on_guild_role_update",
    "on_guild_role_delete", "on_guild_update", "on_member_join", "on_member_remove",
    "on_member_update", "on_voice_state_update",
})
MEMBERSHIP_EVENTS = frozenset({"guild_join", "guild_remove", "disconnect", "resumed"})
PILOT_ENV = "GAMERHQ_STAGING_TENANT_PILOT"


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def event_guild(event_name, args):
    if event_name not in _GUILD_EVENTS:
        return None
    ids = event_guild_ids(args)
    return next(iter(ids)) if len(ids) == 1 else None


def event_guild_ids(args):
    ids = set()
    for item in args:
        guild = item if isinstance(item, discord.Guild) else getattr(item, "guild", None)
        ids.update(ident for ident in (getattr(guild, "id", None), getattr(item, "guild_id", None))
                   if type(ident) is int)
    return ids


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


def pilot_environment(profile, approved, *, primary_only=False):
    """Serialize the existing controller's exact staging pin for stable Compose input."""
    return json.dumps({
        "applicationId": profile["stagingBotId"],
        "guildIds": [str(guild) for guild in sorted(approved)],
        "productionBotId": profile["productionBotId"],
        "productionGuildId": profile["productionGuildId"],
        **({"primaryOnly": True} if primary_only else {}),
    }, sort_keys=True, separators=(",", ":"))


def pilot_routes(value, *, primary, database, get_guild):
    """Read only a dedicated, explicit staging binding from the container environment."""
    from pathlib import Path

    if value is None:
        return None
    try:
        pin = json.loads(value)
        keys = {"applicationId", "guildIds", "productionBotId", "productionGuildId"}
        if (type(pin) is not dict or set(pin) not in (keys, keys | {"primaryOnly"})
                or "primaryOnly" in pin and pin["primaryOnly"] is not True):
            raise ValueError
        app = _discord_id(pin["applicationId"])
        raw_ids = pin["guildIds"]
        if type(raw_ids) is not list or len(raw_ids) != 2:
            raise ValueError
        approved = frozenset(map(_discord_id, raw_ids))
        if (len(approved) != 2 or primary not in approved or app == _discord_id(pin["productionBotId"])
                or _discord_id(pin["productionGuildId"]) in approved):
            raise ValueError
        folder = Path(database).parent / "tenants"
        if folder.is_symlink():
            raise ValueError
        secondary, = approved - {primary}
        parked = secondary if pin.get("primaryOnly") else None
        store = tenants.GuildTenantStore(primary_guild=primary, primary_database=database,
                                         tenant_directory=folder, additional_guilds=() if parked else (secondary,))
    except (TypeError, ValueError, GuildAdmissionError):
        raise GuildAdmissionError("Protected Staging tenant binding is invalid.") from None
    return GuildRoutes(application_id=app, store=store, get_guild=get_guild, inactive_guild=parked)


def verify_guild_permissions(guild_id, application_id, guild, member, roles):
    """Check fresh authenticated REST membership and the Core install mask."""
    from services.discord_permissions import CORE_INSTALL, missing, install_remediation

    try:
        if guild["id"] != str(guild_id) or member["user"]["id"] != str(application_id):
            raise GuildAdmissionError("Discord guild or bot member identity does not match.")
        if not isinstance(roles, list):
            raise TypeError
        grants = {_discord_id(row["id"]): int(row["permissions"]) for row in roles}
        if len(grants) != len(roles):
            raise ValueError
        ids = {guild_id, *map(_discord_id, member["roles"])}
        flags = 0
        for ident in ids:
            flags |= grants[ident]
    except (KeyError, TypeError, ValueError):
        raise GuildAdmissionError("Discord role inventory could not be verified.") from None
    permissions = discord.Permissions(flags)
    if permissions.administrator or str(application_id) == guild.get("owner_id"):
        raise GuildAdmissionError("Staging pilot requires a non-administrator bot role.")
    absent = missing(permissions, CORE_INSTALL)
    if absent:
        raise GuildCorePermissionsError(guild_id, absent, install_remediation(absent))


class GuildRoutes:
    """Starts disabled. Only the trusted Host can submit Discord membership evidence."""

    def __init__(self, *, application_id, store, get_guild, inactive_guild=None):
        self.application_id = _discord_id(str(application_id))
        self.store = store
        self.get_guild = get_guild
        self.allowed = frozenset({store.primary_guild, *store.additional_guilds})
        if inactive_guild is None and len(self.allowed) == 2:
            self.membership = self.allowed
        elif len(self.allowed) == 1 and type(inactive_guild) is int and inactive_guild > 0 and inactive_guild not in self.allowed:
            self.membership = self.allowed | {inactive_guild}
        else:
            raise ValueError("The pilot requires exactly two approved guilds.")
        self._proof = None
        self._revision = 0
        self._tasks = {}
        self.accepting = True

    async def refresh(self, http, *, require_permissions=False):
        token = _membership_read.set(self)
        try:
            await self._refresh(http, require_permissions=require_permissions)
        finally:
            _membership_read.reset(token)

    async def _refresh(self, http, *, require_permissions):
        self.invalidate()
        revision = self._revision
        identity = await http.request(Route("GET", "/users/@me"))
        guilds = await http.request(Route("GET", "/users/@me/guilds"), params={"limit": 200})
        verify_membership(self.application_id, self.membership, identity, guilds)
        if require_permissions:
            for guild_id in sorted(self.allowed):
                kwargs = {"guild_id": guild_id}
                guild = await http.request(Route("GET", "/guilds/{guild_id}", **kwargs))
                member = await http.request(Route("GET", "/guilds/{guild_id}/members/{user_id}",
                                                  user_id=self.application_id, **kwargs))
                roles = await http.request(Route("GET", "/guilds/{guild_id}/roles", **kwargs))
                verify_guild_permissions(guild_id, self.application_id, guild, member, roles)
        if revision != self._revision:
            raise GuildAdmissionError("Membership changed during verification.")
        self.verify(identity, guilds)

    def verify(self, bot_user, guilds):
        """Check the exact set from GET /users/@me/guilds?limit=200 and the bot identity."""
        self.invalidate()
        verify_membership(self.application_id, self.membership, bot_user, guilds)
        self._proof = object()

    def invalidate(self):
        self._proof = None
        self._revision += 1
        current_task = _task()
        for task in tuple(self._tasks):
            if task is not current_task:
                task.cancel()

    def bootstrap_scope(self):
        """Use the primary installation for extension registration after REST proof."""
        proof = self._proof

        def validate():
            if proof is None or self._proof is not proof:
                raise GuildAdmissionError("Startup membership evidence expired.")

        validate()
        return self.store.scope(self.store.primary_guild, validate=validate)

    def require_runtime_scope(self, guild_id):
        bound = tenants.current()
        if bound is None:
            raise GuildAdmissionError("Pilot guild operations require a verified tenant scope.")
        tenants.require_guild(guild_id)
        self._guild(guild_id)

    async def discord_request(self, request, route, get_channel, **kwargs):
        """Guard the shared discord.py client before any guild resource request."""
        read = route.method == "GET"
        if read and route.path in {"/users/@me", "/users/@me/guilds", "/oauth2/applications/@me",
                                    "/gateway", "/gateway/bot"}:
            return await request(route, **kwargs)
        guild_id = route.guild_id
        if (read and _membership_read.get() is self and guild_id in self.allowed
                and route.path in {"/guilds/{guild_id}", "/guilds/{guild_id}/members/{user_id}",
                                   "/guilds/{guild_id}/roles"}):
            return await request(route, **kwargs)
        binding = tenants.current()
        if binding is None:
            raise GuildAdmissionError("Discord requests require a verified guild scope.")
        self.require_runtime_scope(binding.guild_id)
        if guild_id is not None and guild_id != binding.guild_id:
            raise GuildAdmissionError("Discord request targets another guild.")
        if route.channel_id is not None:
            channel = get_channel(route.channel_id)
            if channel is None:
                raw = await request(Route("GET", "/channels/{channel_id}", channel_id=route.channel_id))
                if raw.get("guild_id") != str(binding.guild_id) or raw.get("id") != str(route.channel_id):
                    raise GuildAdmissionError("Discord channel ownership could not be verified.")
                self.require_runtime_scope(binding.guild_id)
                if read and route.path == "/channels/{channel_id}":
                    return raw
            elif getattr(getattr(channel, "guild", None), "id", None) != binding.guild_id:
                raise GuildAdmissionError("Discord channel belongs to another guild.")
        elif guild_id is None:
            raise GuildAdmissionError("Discord request has no verifiable guild resource.")
        return await request(route, **kwargs)

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
        if not self.accepting:
            raise GuildAdmissionError("Guild runtime is still starting.")
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
            if self.accepting:
                guild_ids = sorted(self.allowed)
                results = await asyncio.gather(*(self.run_due(runtime, guild_id) for guild_id in guild_ids),
                                               return_exceptions=True)
                for guild_id, result in zip(guild_ids, results, strict=True):
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
        if not routes.accepting:
            return

        async def tick(guild_id):
            with routes.scope(guild_id):
                await callback(cog)

        results = await asyncio.gather(*(asyncio.create_task(tick(guild_id), context=Context())
                                         for guild_id in sorted(routes.allowed)),
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


def add_shared_entry_view(bot, view):
    """Register a stateless entry point that resolves all state from the interaction."""
    if getattr(bot, "guild_routes", None) is not None:
        if not view.is_persistent() or view.__discord_ui_modal__:
            raise ValueError("Only persistent entry views can be shared across guilds.")
        view._gamerhq_shared_entry = True
    bot.add_view(view)


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
            shared_entry = getattr(view, "_gamerhq_shared_entry", False) and message_id is None

            async def run(*args):
                interaction = args[0 if modal else 1]
                routes = getattr(interaction.client, "guild_routes", None)
                if routes is not None:
                    expected = origin if origin is not None else routes.store.primary_guild
                    if not shared_entry and interaction.guild_id != expected:
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
        client = self._get_client()
        routes = getattr(client, "guild_routes", None)
        if routes is not None:
            try:
                if not routes.accepting or _discord_id(data.get("application_id")) != routes.application_id:
                    return
                routes._guild(_discord_id(data.get("guild_id")))
            except GuildAdmissionError:
                return
        elif (primary := getattr(client, "legacy_guild_id", None)) is not None:
            guild = data.get("guild_id")
            if guild is not None and guild != str(primary):
                return
        return super().parse_interaction_create(data)
