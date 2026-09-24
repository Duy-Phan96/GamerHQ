"""One trusted paid-deal listener; never edit the source bot's message."""
import logging
import discord
from discord.ext import commands
from services.gocdkeys_service import GoCdKeysService


class GoCdKeysWatcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.service = GoCdKeysService()

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.service.handle(message)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        import config
        from services.instant_gaming_service import resolve
        if not config.GOCDKEYS_ENABLED or payload.guild_id != config.GUILD_ID:
            return
        # Ignore pin-only updates. Partial content updates require a fresh source.
        if not {'content', 'embeds'} & payload.data.keys():
            return
        try:
            guild = self.bot.get_guild(payload.guild_id)
            channel = resolve(guild, 'gaming-deals', mapped_only=True) if guild else None
            if channel and channel.id == payload.channel_id:
                message = await channel.fetch_message(payload.message_id)
                await self.service.handle(message)
        except Exception as exc:
            logging.getLogger(__name__).debug('[gocdkeys] skipped: edit lookup (%s)', type(exc).__name__)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        await self.service.handle_delete(self.bot.get_guild(payload.guild_id), payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        guild = self.bot.get_guild(payload.guild_id)
        for message_id in payload.message_ids:
            await self.service.handle_delete(guild, payload.channel_id, message_id)


async def setup(bot):
    await bot.add_cog(GoCdKeysWatcher(bot))
