"""Consistent, private error replies; technical exceptions stay in runtime logs."""
import logging
import discord
from discord import app_commands

log = logging.getLogger(__name__)


async def report_error(interaction, error, context='operation'):
    log.error('GamerHQ failed action=%s interaction=%s actor=%s guild=%s', context, getattr(interaction,'id',None), getattr(interaction.user,'id',None), getattr(interaction,'guild_id',None), exc_info=(type(error),error,error.__traceback__))
    text=f'❌ {context} could not finish. Refresh the panel or run `/server health` before retrying.'
    if interaction.response.is_done():
        await interaction.followup.send(text,ephemeral=True)
    else:
        await interaction.response.send_message(text,ephemeral=True)


class SafeView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        label = getattr(item, 'label', None) or 'This action'
        await report_error(interaction, error, label)


async def command_error(self, interaction, error):
    await application_error(interaction, error)


async def application_error(interaction, error):
    """Expected command failures are private, actionable replies, not tracebacks."""
    if isinstance(error, app_commands.CommandOnCooldown):
        text = f'Please try again in {max(1, int(error.retry_after) + 1)} seconds.'
    elif isinstance(error, app_commands.BotMissingPermissions):
        text = 'The bot is missing required permissions. Please contact staff.'
    elif isinstance(error, app_commands.CheckFailure):
        text = 'You do not have access to this command here.'
    else:
        name = getattr(getattr(interaction, 'command', None), 'qualified_name', None)
        await report_error(interaction, getattr(error, 'original', error), f'`/{name}`' if name else 'This command')
        return
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def tree_error(interaction, error):
    # discord.py invokes the tree handler after local/cog handlers as well.
    command = interaction.command
    if command and command._has_any_error_handlers():
        return
    await application_error(interaction, error)


async def check_admin(interaction, guild=None):
    """Recheck current membership before an actor-bound admin mutation."""
    from services.authorization_service import authorized
    guild = guild if guild is not None else interaction.guild
    if interaction.guild and guild and interaction.guild.id == guild.id and authorized(guild, interaction.user):
        return True
    text = '❌ Administrator access is required. Reopen this panel after your permissions are restored.'
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)
    return False
