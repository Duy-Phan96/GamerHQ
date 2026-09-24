"""One trusted paid-deal listener; never edit the source bot's message."""
import logging
import discord
from discord import app_commands
from discord.ext import commands
from services.gocdkeys_service import GoCdKeysService
from services.game_area_cleanup import authorized


class BackfillView(discord.ui.View):
    def __init__(self, service, guild, actor_id, limit, plan=None):
        super().__init__(timeout=180)
        self.service, self.guild, self.actor_id = service, guild, actor_id
        self.limit, self.plan, self.used = limit, plan, False
        self.proceed.label = 'Run Backfill' if plan is not None else 'Preview'

    async def interaction_check(self, interaction):
        if (not interaction.guild or interaction.guild.id != self.guild.id
                or interaction.user.id != self.actor_id
                or not authorized(interaction.guild, interaction.user)):
            await interaction.response.send_message('Owner/admin access required for this session.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Preview', style=discord.ButtonStyle.primary)
    async def proceed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.interaction_check(interaction):
            return
        if self.used:
            return await interaction.response.send_message('This step has already been used.', ephemeral=True)
        self.used = True
        self.stop()
        await interaction.response.defer()
        await interaction.edit_original_response(content='Checking recent Gaming Deals…', view=None)
        try:
            if self.plan is None:
                plan = await self.service.preview_backfill(self.guild, self.limit)
                text = (f'🎮 **Backfill Preview**\nMessages scanned: {plan["scanned"]}\n'
                        f'Supported deals: {plan["supported"]}\nAlready enriched: {plan["enriched"]}\n'
                        f'Retained delivery claims: {plan["retained"]}\nMissing comparison: {len(plan["candidates"])}\n'
                        f'Skipped: {plan["skipped"]}\n\nNo changes made. Product pages are validated only on Run; unresolved games are skipped.')
                view = BackfillView(self.service, self.guild, self.actor_id, self.limit, plan)
                view.proceed.disabled = not plan['candidates']
                await interaction.edit_original_response(content=text, view=view)
            else:
                result = await self.service.run_backfill(self.guild, self.plan)
                await interaction.edit_original_response(content=(
                    f'🎮 **Backfill complete**\nCreated: {result["created"]}\n'
                    f'Already handled: {result["retained"]}\nSkipped/unresolved: {result["skipped"]}\n'
                    f'Uncertain delivery: {result["uncertain"]}\n'
                    'Uncertain/reserved claims are never automatically retried. Check /server health and private logs for skips.'), view=None)
        except (ValueError, discord.HTTPException) as exc:
            logging.getLogger(__name__).warning('[gocdkeys] backfill unavailable (%s)', type(exc).__name__)
            await interaction.edit_original_response(content='Backfill unavailable. Check configuration, /server health and channel access, then create a new preview.', view=None)

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.interaction_check(interaction):
            return
        if self.used:
            return await interaction.response.send_message('This step has already been used.', ephemeral=True)
        self.used = True
        self.stop()
        await interaction.response.edit_message(content='Backfill cancelled. No comparisons posted.', view=None)


class GoCdKeysWatcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.service = GoCdKeysService()

    deals = app_commands.Group(name='deals', description='Gaming deal administration',
                               default_permissions=discord.Permissions(administrator=True))

    @deals.command(name='backfill', description='Owner/admin: preview missing comparisons on recent gaming deals.')
    @app_commands.guild_only()
    @app_commands.choices(count=[app_commands.Choice(name=str(n), value=n) for n in (25, 50, 100)])
    async def backfill(self, interaction: discord.Interaction, count: int = 50):
        if not interaction.guild or not authorized(interaction.guild, interaction.user):
            return await interaction.response.send_message('Owner or administrator access required.', ephemeral=True)
        try:
            channel = self.service.backfill_channel(interaction.guild)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.send_message(
            f'🎮 **Gaming Deals Backfill**\nChannel: {channel.mention}\nScan: last {count} messages',
            view=BackfillView(self.service, interaction.guild, interaction.user.id, count), ephemeral=True)

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
