"""Guild admission and scoped ingress for a reviewed multi-guild cohort."""
from contextlib import ExitStack, contextmanager
import re

from discord import app_commands
from database import tenants


class GuildAdmissionError(PermissionError):
    """No current proof authorizes the operation on this guild."""


def _discord_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise GuildAdmissionError("Invalid Discord identity in membership evidence.")
    return int(value)


class GuildRoutes:
    """Starts disabled. Only the trusted Host can submit Discord membership evidence."""

    def __init__(self, *, application_id, store, get_guild):
        self.application_id = _discord_id(str(application_id))
        self.store = store
        self.get_guild = get_guild
        self.allowed = frozenset({store.primary_guild, *store.additional_guilds})
        if len(self.allowed) >= 200:
            raise ValueError("Pilot cohorts must fit in a complete Discord membership response.")
        self._proof = None

    def verify(self, bot_user, guilds):
        """Check the exact set from GET /users/@me/guilds?limit=200 and the bot identity."""
        self.invalidate()
        if (not isinstance(bot_user, dict) or bot_user.get("bot") is not True
                or _discord_id(bot_user.get("id")) != self.application_id):
            raise GuildAdmissionError("Discord application identity could not be verified.")
        if not isinstance(guilds, list) or len(guilds) != len(self.allowed):
            raise GuildAdmissionError("Discord memberships differ from the approved guilds.")
        observed = {_discord_id(row.get("id") if isinstance(row, dict) else None) for row in guilds}
        if observed != self.allowed:
            raise GuildAdmissionError("Discord memberships differ from the approved guilds.")
        self._proof = object()

    def invalidate(self):
        self._proof = None

    def require_runtime_scope(self, guild_id):
        bound = tenants.current()
        if bound is None:
            if guild_id != self.store.primary_guild:
                raise GuildAdmissionError("Additional guild operations require a verified tenant scope.")
            from database import db
            if db.current_path().resolve() != self.store.primary_database:
                raise GuildAdmissionError("The legacy database does not match the primary guild binding.")
            return
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
            yield binding

    @contextmanager
    def interaction(self, interaction):
        guild_id = interaction.guild_id
        guild = self._guild(guild_id)
        if (interaction.application_id != self.application_id or interaction.guild is not guild
                or guild.get_member(interaction.user.id) is None):
            raise GuildAdmissionError("Interaction does not belong to this guild.")
        with self.scope(guild_id) as binding:
            yield binding

    async def restore(self, runtime, guild_id):
        with self.scope(guild_id):
            return await runtime.restore_guild(guild_id=guild_id)

    async def run_due(self, runtime, guild_id, **kwargs):
        with self.scope(guild_id):
            return await runtime.scheduler.run_due(**kwargs)


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
