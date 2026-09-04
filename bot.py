import discord
from discord.ext import commands

from config import TOKEN, GUILD_ID
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
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        from config import DB_PATH
        print(f"[GamerHQ] Runtime database: {DB_PATH.resolve()}")
        db.init_db()
        db.seed_catalog()

        await self.load_extension("cogs.games")
        await self.load_extension("cogs.voice")
        await self.load_extension("cogs.server")
        await self.load_extension("cogs.roles")
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
            await refresh_staff_command_guide(bot, guild)
        except Exception as exc:
            # Documentation refresh must never prevent the bot from coming online.
            print(f"Staff command guide refresh skipped: {exc}")

        try:
            await refresh_community_command_guide(bot, guild)
        except Exception as exc:
            print(f"Community command guide refresh skipped: {exc}")

        try:
            await refresh_streamer_command_guide(bot, guild)
        except Exception as exc:
            print(f"Streamer command guide refresh skipped: {exc}")

        # Refresh GamerHQ-owned fixed server copy. This also migrates the old
        # introductions embed to the current managed markdown message in place.
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


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing in .env")

bot.run(TOKEN)
