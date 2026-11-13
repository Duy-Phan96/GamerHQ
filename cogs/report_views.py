"""Copyable, private Discord reports with an optional full text export."""
from io import BytesIO
import logging

import discord

log = logging.getLogger(__name__)
REPORT_ACTIONS = {'gamerhq:report:copy': 'Copy Report', 'gamerhq:report:full': 'Full Report',
                  'gamerhq:report:previous': 'Previous Report Page', 'gamerhq:report:next': 'Next Report Page'}


async def report_error(interaction, error, action):
    code = error.code if isinstance(error, discord.HTTPException) else None
    acknowledged = interaction.response.is_done()
    log.warning('Discord report action=%s interaction=%s guild=%s acknowledged=%s error=%s code=%s',
                action, getattr(interaction, 'id', None), getattr(interaction, 'guild_id', None),
                acknowledged, type(error).__name__, code)
    if acknowledged or code in (10062, 40060) or isinstance(error, (discord.InteractionResponded, TimeoutError, OSError)):
        return
    try:
        await interaction.response.send_message(
            f'{action} could not finish. Reopen the panel and try again. This report action is read-only.',
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except (discord.HTTPException, discord.InteractionResponded, TimeoutError, OSError):
        return


class ReportErrors:
    """Handle report callbacks separately from the parent's setup actions."""

    async def on_error(self, interaction, error, item):
        action = REPORT_ACTIONS.get(getattr(item, 'custom_id', None))
        if action:
            await report_error(interaction, error, action)
        else:
            await super().on_error(interaction, error, item)


def pages(text, limit=1850):
    remaining = text.replace('```', '``\u200b`')
    while len(remaining.encode('utf-16-le')) // 2 > limit:
        end = len(remaining.encode('utf-16-le')[:2 * limit].decode('utf-16-le', errors='ignore'))
        newline = remaining.rfind('\n', 0, end)
        split = newline + 1 if newline > 0 else end
        yield remaining[:split]
        remaining = remaining[split:]
    yield remaining


async def copy_report(interaction, text):
    chunks = list(pages(text))
    await interaction.response.send_message(ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                                            content=f'```text\n{chunks[0]}\n```',
                                            view=ReportPages(interaction, chunks) if len(chunks) > 1 else None)


async def full_report(interaction, text, filename):
    await interaction.response.send_message(ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                                            file=discord.File(BytesIO(text.encode('utf-8')), filename=filename))


class ReportPages(ReportErrors, discord.ui.View):
    def __init__(self, interaction, chunks):
        super().__init__(timeout=240)
        self.guild_id = interaction.guild.id
        self.actor_id = interaction.user.id
        self.chunks = chunks
        self.index = 0
        self.previous.disabled = True

    async def interaction_check(self, interaction):
        allowed = (interaction.guild_id == self.guild_id and interaction.user.id == self.actor_id
                   and self.actor_id == interaction.guild.owner_id)
        if not allowed:
            await interaction.response.send_message('This report is private.', ephemeral=True)
        return allowed

    async def turn(self, interaction, offset):
        previous = self.index
        self.index = max(0, min(previous + offset, len(self.chunks) - 1))
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.chunks) - 1
        try:
            await interaction.response.edit_message(content=f'```text\n{self.chunks[self.index]}\n```', view=self,
                                                    allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            self.index = previous
            self.previous.disabled = previous == 0
            self.next.disabled = previous == len(self.chunks) - 1
            raise

    @discord.ui.button(label='Previous Page', custom_id='gamerhq:report:previous')
    async def previous(self, interaction, button):
        await self.turn(interaction, -1)

    @discord.ui.button(label='Next Page', custom_id='gamerhq:report:next')
    async def next(self, interaction, button):
        await self.turn(interaction, 1)
