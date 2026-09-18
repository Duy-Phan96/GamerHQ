"""Consistent, private error replies; technical exceptions stay in runtime logs."""
import logging
import discord

log = logging.getLogger(__name__)


async def report_error(interaction, error, context='operation'):
    log.error('GamerHQ failed action=%s actor=%s guild=%s', context, getattr(interaction.user,'id',None), getattr(interaction,'guild_id',None), exc_info=(type(error),error,error.__traceback__))
    text='❌ This action could not finish. Some changes may already have applied. Refresh the panel or run `/server health` before retrying.'
    if interaction.response.is_done():
        await interaction.followup.send(text,ephemeral=True)
    else:
        await interaction.response.send_message(text,ephemeral=True)


class SafeView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        await report_error(interaction,error,type(self).__name__)


async def command_error(self, interaction, error):
    await report_error(interaction,error,type(self).__name__)
