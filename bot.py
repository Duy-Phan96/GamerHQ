import logging
import asyncio
from contextlib import suppress
import discord
from discord.ext import commands

try:
    from config import TOKEN, GUILD_ID, validate_startup, ConfigurationError
except ValueError as error:
    raise SystemExit(f"Configuration error: {error}") from None
from database import db
from services.command_guide_service import (
    refresh_staff_command_guide,
    refresh_community_command_guide,
    refresh_streamer_command_guide,
)


class GamerHQBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        from config import GOCDKEYS_ENABLED, GOCDKEYS_AUTOMATIC_SUPPORTED
        intents.message_content = GOCDKEYS_ENABLED and GOCDKEYS_AUTOMATIC_SUPPORTED
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self.health_task = None

    async def setup_hook(self):
        # Container-only, ephemeral heartbeat. Local development needs no /tmp.
        from pathlib import Path
        if Path('/.dockerenv').exists():
            from tools.container_health import heartbeat
            self.health_task = asyncio.create_task(heartbeat(self))
        from config import DB_PATH
        print(f"[GamerHQ] Runtime database: {DB_PATH.resolve()}")
        db.init_db()
        db.seed_catalog()

        await self.load_extension("cogs.games")
        await self.load_extension("cogs.voice")
        await self.load_extension("cogs.voice_controls")
        await self.load_extension("cogs.area")
        await self.load_extension("cogs.server")
        await self.load_extension("cogs.server_changes")
        await self.load_extension("cogs.gocdkeys")
        await self.load_extension("cogs.roles")
        await self.load_extension("cogs.suggestions")
        await self.load_extension("cogs.tickets")
        await self.load_extension("cogs.lfg")
        await self.load_extension("cogs.streamer")

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
        logging.getLogger(__name__).warning("GamerHQ startup: %s extensions, %s persistent views. Use /server health for acceptance diagnostics; owner /server setup for repairs.", len(self.extensions), len(self.persistent_views))

    async def close(self):
        # Each owned resource must close even if an earlier cleanup fails.
        try:
            try:
                if self.health_task:
                    self.health_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.health_task
            finally:
                if getattr(self, 'twitch_hub', None):
                    await self.twitch_hub.close()
        finally:
            await super().close()


bot = GamerHQBot()


@bot.event
async def on_ready():
    print(f"GamerHQ Bot is online as {bot.user}!")
    for guild in bot.guilds:
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

        # Keep the private staff command reference synchronized with the
        # currently registered /server and /game-admin commands.
        try:
            from services.onboarding_service import alias, unique
            existing = unique([c for c in guild.text_channels if alias(c.name) in {'mod-commands','staff-commands','moderator-commands','mods-commands'}], 'mod-commands')
            if existing:
                await refresh_staff_command_guide(bot, guild, channel=existing)
            else:
                print('Staff guide not found; review /server health. No structure created.')
        except Exception as exc:
            # Documentation refresh must never prevent the bot from coming online.
            print(f"Staff command guide refresh skipped: {exc}")

        try:
            await refresh_community_command_guide(bot, guild)
        except Exception as exc:
            print(f"Community command guide refresh skipped: {exc}")

        try:
            from services.onboarding_service import unique
            existing = unique(guild.text_channels, 'streamer-commands')
            if existing:
                await refresh_streamer_command_guide(bot, guild, channel=existing)
            else:
                print('Streamer commands missing; review /server health. No structure created.')
        except Exception as exc:
            print(f"Streamer command guide refresh skipped: {exc}")

        # Refresh GamerHQ-owned fixed server copy. This also migrates the old
        # onboarding pins without recreating guides in introductions/newbies.
        server_cog = bot.get_cog("ServerAdmin")
        if server_cog:
            try:
                await server_cog.refresh_default_managed_messages(guild)
            except Exception as exc:
                print(f"Managed server message refresh skipped: {exc}")

        try:
            from cogs.server import refresh_lfg_guide_message, refresh_future_community_messages
            await refresh_lfg_guide_message(guild)
            await refresh_future_community_messages(guild)
        except Exception as exc:
            print(f"Community guide refresh skipped: {exc}")

        # Repair/update the public selector with its required persistent views.
        try:
            from cogs.games import GameCategoryView, ChooseGamesButtons
            from services.game_service import refresh_choose_games_message
            games_cog = bot.get_cog("Games")
            await refresh_choose_games_message(
                bot,
                view=GameCategoryView,
                intro_view=lambda: ChooseGamesButtons(games_cog),
            )
        except Exception as exc:
            print(f"Choose Your Games refresh skipped: {exc}")


if __name__ == "__main__":
    try:
        validate_startup()
    except ConfigurationError as error:
        raise SystemExit(f"Configuration error: {error}") from None
    bot.run(TOKEN)
