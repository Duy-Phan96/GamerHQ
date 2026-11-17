import logging
import asyncio
from contextlib import suppress
import aiohttp
import discord
from discord.ext import commands

try:
    from config import TOKEN, GUILD_ID, validate_startup, ConfigurationError
except ValueError as error:
    raise SystemExit(f"Configuration error: {error}") from None
from database import db, tenants
from services.tenant_routing import GuildAdmissionError, GuildCommandTree, GuildConnectionState, MEMBERSHIP_EVENTS, event_guild


class GamerHQBot(commands.Bot):
    @property
    def guilds(self):
        if getattr(self, 'guild_routes', None) is None:
            return super().guilds
        tenant = tenants.current()
        guild = self.get_guild(tenant.guild_id) if tenant else None
        return [guild] if guild else []

    def _get_state(self, **options):
        return GuildConnectionState(dispatch=self.dispatch, handlers=self._handlers, hooks=self._hooks,
                                    http=self.http, **options)

    def dispatch(self, event, /, *args, **kwargs):
        routes = getattr(self, 'guild_routes', None)
        if routes is not None and event in MEMBERSHIP_EVENTS:
            routes.invalidate()
        if routes is not None and event in {'ready', 'resumed', 'guild_join', 'guild_remove'}:
            previous = getattr(self, '_pilot_ready_task', None)
            if previous is not None and not previous.done():
                previous.cancel()
            self._pilot_ready_task = self.loop.create_task(self._prepare_pilot_guilds())
        return super().dispatch(event, *args, **kwargs)

    async def _prepare_pilot_guilds(self):
        routes = self.guild_routes
        try:
            await routes.refresh(self.http)
            await routes.sync_commands(self.tree)
            for guild_id in sorted(routes.allowed):
                await routes.restore(self.skill_runtime, guild_id)
            await self.skill_runtime.start_scheduler()
        except GuildAdmissionError:
            logging.getLogger(__name__).warning("Guild pilot paused pending exact membership and storage verification.")
        except Exception:
            routes.invalidate()
            logging.getLogger(__name__).exception("Guild pilot startup failed.")

    async def _run_event(self, coro, event_name, *args, **kwargs):
        routes = getattr(self, 'guild_routes', None)
        if routes is None:
            return await super()._run_event(coro, event_name, *args, **kwargs)
        guild_id = event_guild(event_name, args)
        if guild_id is None:
            return  # Process-wide or ambiguous listeners are held by the pilot.
        try:
            scope = routes.interaction(args[0]) if event_name == 'on_interaction' else routes.scope(guild_id)
            with scope:
                return await super()._run_event(coro, event_name, *args, **kwargs)
        except GuildAdmissionError:
            return

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        from config import GOCDKEYS_ENABLED, GOCDKEYS_AUTOMATIC_SUPPORTED
        intents.message_content = GOCDKEYS_ENABLED and GOCDKEYS_AUTOMATIC_SUPPORTED
        intents.voice_states = True
        trace = aiohttp.TraceConfig()

        async def verify_active_scope(*_):
            # aiohttp starts each Discord HTTP attempt, including transport retries.
            tenants.current()

        trace.on_request_start.append(verify_active_scope)
        super().__init__(command_prefix="!", intents=intents, tree_cls=GuildCommandTree, http_trace=trace)
        from services.operation_context import install_http_metrics
        install_http_metrics(self.http)
        from services.response_service import tree_error
        self.tree.on_error = tree_error
        self.health_task = None
        self.operational_log_started = set()
        self.skill_runtime = None
        self.web_api_server = None

    async def setup_hook(self):
        routes = getattr(self, "guild_routes", None)
        if routes is None:
            return await self._prepare_application()
        # Local primary schema and extension setup precede Discord membership proof.
        with routes.store.scope(routes.store.primary_guild):
            await self._prepare_application()

    async def _prepare_application(self):
        # Container-only, ephemeral heartbeat. Local development needs no /tmp.
        from pathlib import Path
        if Path('/.dockerenv').exists():
            from tools.container_health import heartbeat
            self.health_task = asyncio.create_task(heartbeat(self))
        from config import DB_PATH
        print(f"[GamerHQ] Runtime database: {DB_PATH.resolve()}")
        db.init_db()
        db.seed_catalog()

        from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
        from skills import first_party_skills
        from hosts.gamerhq.skill_packages import configured_skill_ids, load_external_skill_packages
        from config import EXTERNAL_SKILLS
        self.skill_runtime = GamerHQSkillRuntime(self)
        for skill in first_party_skills():
            self.skill_runtime.register(skill, source_kind="built-in")
        load_external_skill_packages(
            self.skill_runtime,
            configured_skill_ids(EXTERNAL_SKILLS),
        )
        await self.skill_runtime.register_all()

        from config import (
            GAMERHQ_WEB_API_ENABLED,
            GAMERHQ_WEB_API_HOST,
            GAMERHQ_WEB_API_PORT,
            GAMERHQ_WEB_API_SECRET,
        )
        if GAMERHQ_WEB_API_ENABLED:
            from hosts.gamerhq.web_api import GamerHQWebApiServer
            self.web_api_server = GamerHQWebApiServer(
                bot=self,
                skill_runtime=self.skill_runtime,
                shared_secret=GAMERHQ_WEB_API_SECRET,
                host=GAMERHQ_WEB_API_HOST,
                port=GAMERHQ_WEB_API_PORT,
            )
            await self.web_api_server.start()
            print(
                f"[GamerHQ] Internal Web API listening on "
                f"{GAMERHQ_WEB_API_HOST}:{GAMERHQ_WEB_API_PORT}"
            )

        await self.load_extension("cogs.games")
        await self.load_extension("cogs.voice")
        await self.load_extension("cogs.voice_controls")
        await self.load_extension("cogs.area")
        await self.load_extension("cogs.server")
        await self.load_extension("cogs.server_changes")
        await self.load_extension("cogs.owner_changelog")
        await self.load_extension("cogs.gocdkeys")
        await self.load_extension("cogs.roles")
        await self.load_extension("cogs.profile")
        await self.load_extension("cogs.progression_activity")
        await self.load_extension("cogs.suggestions")
        await self.load_extension("cogs.tickets")
        # Event Manager adapters are selected by the installed public Skill,
        # never by a second Host implementation of its domain.
        from hosts.gamerhq.event_cutover import owns_events
        if self.skill_runtime.registry.contains("event-manager") or owns_events(GUILD_ID):
            await self.load_extension("cogs.event_manager")
        else:
            await self.load_extension("cogs.lfg")
            await self.load_extension("cogs.lobby_admin")
        await self.load_extension("cogs.streamer")

        if getattr(self, "guild_routes", None) is not None:
            return  # Guild-specific command writes wait for fresh membership proof.
        guild = discord.Object(id=GUILD_ID)

        # Aktuelle Commands sofort auf GamerHQ registrieren
        self.tree.copy_global_to(guild=guild)
        guild_synced = await self.tree.sync(guild=guild)

        # Alte globale Commands bei Discord entfernen,
        # damit sie nicht zusätzlich zu den Guild-Commands erscheinen.
        self.tree.clear_commands(guild=None)
        global_synced = await self.tree.sync()

        print(f"Synced {len(guild_synced)} command group(s) to GamerHQ.")
        print(f"Cleared global commands: {len(global_synced)} remaining.")
        logging.getLogger(__name__).warning("GamerHQ startup: %s extensions, %s persistent views. Administration: /server manage; technical tools: /server dev.", len(self.extensions), len(self.persistent_views))

    async def close(self):
        # Each owned resource must close even if an earlier cleanup fails.
        try:
            try:
                pilot_task = getattr(self, '_pilot_ready_task', None)
                if pilot_task is not None:
                    pilot_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await pilot_task
                if self.health_task:
                    self.health_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.health_task
            finally:
                if getattr(self, 'twitch_hub', None):
                    await self.twitch_hub.close()
        finally:
            try:
                web_api_server = getattr(self, "web_api_server", None)
                if web_api_server is not None:
                    await web_api_server.close()
            finally:
                try:
                    skill_runtime = getattr(self, 'skill_runtime', None)
                    if skill_runtime is not None:
                        await skill_runtime.close()
                finally:
                    await super().close()


bot = GamerHQBot()


@bot.event
async def on_ready():
    print(f"GamerHQ Bot is online as {bot.user}!")
    if bot.skill_runtime is not None:
        try:
            await bot.skill_runtime.start_scheduler()
        except Exception:
            logging.getLogger(__name__).exception('Skill Scheduler failed to start.')

    for guild in bot.guilds:
        if guild.id not in bot.operational_log_started:
            bot.operational_log_started.add(guild.id)
            from services.server_log_service import startup
            try:
                await startup(guild, bot)
            except Exception:
                logging.getLogger(__name__).warning('Startup diagnostics unavailable; review /server manage.')
        if bot.skill_runtime is not None and bot.skill_runtime.registry.ids():
            try:
                await bot.skill_runtime.restore_guild(guild_id=guild.id)
            except Exception:
                logging.getLogger(__name__).exception('Skill Runtime restore failed for guild %s.', guild.id)

        # Legacy channel migration is an explicit maintenance operation only.
        # Startup must not delete DB-linked game channels or rename community channels.
        try:
            legacy_count = sum(bool(game.get("memes_channel_id")) for game in db.get_area_games())
            if legacy_count:
                print(f"[GamerHQ] {legacy_count} legacy game memes link(s) need staff review; nothing changed.")
        except Exception as exc:
            print(f"[GamerHQ] Legacy channel report failed: {exc}")

        voice_cog = bot.get_cog("VoiceGenerator")
        if voice_cog:
            await voice_cog.cleanup_empty_temp_channels(guild)

        # Managed structure/message maintenance is now an explicit admin operation.
        # Persistent handlers are registered by cogs; ordinary member/event lifecycles continue.
        print('[GamerHQ] Managed resources unchanged at startup. Administration: /server manage.')


if __name__ == "__main__":
    print('[GamerHQ] Run only ONE active process against this live guild. Stop the local bot before starting the VPS bot; SQLite stores and process locks are separate.')
    try:
        validate_startup()
    except ConfigurationError as error:
        raise SystemExit(f"Configuration error: {error}") from None
    from tools.instance_lock import instance_lock
    from config import DB_PATH
    try:
        with instance_lock(DB_PATH):
            bot.run(TOKEN)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
