"""Owner-only, scoped support entry checks; no global setup or cleanup."""
import time

import discord
from services import ticket_entry_service as entries
from services.response_service import SafeView, report_error


def safe(value, limit=1100):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))[:limit]


async def open_management(interaction):
    if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message('Only the server owner can check and repair these entry bindings.', ephemeral=True)
    await interaction.response.send_message(
        '# Support & Requests\nCheck the existing entry first. No tickets, channels, messages or permissions are changed by a check.',
        view=EntryChecks(interaction.guild.id, interaction.user.id), ephemeral=True)


class EntryChecks(SafeView):
    def __init__(self, guild_id, actor_id, kind=None, hint=None):
        super().__init__(timeout=180)
        self.guild_id, self.actor_id, self.expires = guild_id, actor_id, time.monotonic() + 180
        for selected in ((kind,) if kind else entries.KINDS):
            button = discord.ui.Button(label='Check ' + ('Need Support' if selected == 'support' else 'Electricity'))
            async def check(interaction, selected=selected):
                if not await self.interaction_check(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                draft = await entries.preview(interaction.guild, interaction.user, selected, hint=hint)
                if not await self.still_owner(interaction):
                    return
                await interaction.edit_original_response(content=render(draft),
                    view=EntryReview(draft), allowed_mentions=discord.AllowedMentions.none())
            button.callback = check
            self.add_item(button)

    async def still_owner(self, interaction):
        ok = (interaction.guild and interaction.guild.id == self.guild_id
              and interaction.user.id == self.actor_id == interaction.guild.owner_id
              and time.monotonic() < self.expires)
        if not ok:
            text = 'This owner check expired or server ownership changed. Reopen Support & Requests.'
            if interaction.response.is_done():
                await interaction.edit_original_response(content=text, view=None)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        return bool(ok)

    async def interaction_check(self, interaction):
        return await self.still_owner(interaction)


class EntryReview(EntryChecks):
    def __init__(self, draft):
        # This is an expiring confirmation, not a persistent public action.
        SafeView.__init__(self, timeout=180)
        self.guild_id, self.actor_id = draft['guild_id'], draft['actor_id']
        self.expires, self.used, self.draft = time.monotonic() + 180, False, draft
        if draft['repairable']:
            button = discord.ui.Button(label='Confirm Entry Repair', style=discord.ButtonStyle.primary)
            button.callback = self.confirm
            self.add_item(button)
        close = discord.ui.Button(label='Close')
        close.callback = self.close
        self.add_item(close)

    async def confirm(self, interaction):
        if not await self.interaction_check(interaction):
            return
        if self.used:
            return await interaction.response.send_message('This review was already submitted. Open a new check.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            row = await entries.repair(interaction.guild, interaction.user, self.draft, confirmed=True)
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label='Open repaired entry',
                url=f'https://discord.com/channels/{self.guild_id}/{row["channel_id"]}/{row["message_id"]}'))
            await interaction.edit_original_response(
                content='✅ Entry bindings repaired. The existing text, buttons and ticket history were preserved. Reopen the entry and try its button.',
                view=view)
        except (entries.ServerMessageError, discord.HTTPException) as exc:
            text = str(exc) if isinstance(exc, entries.ServerMessageError) else 'Discord could not verify the repair. Reopen the check; no replacement was created.'
            await interaction.edit_original_response(content='⚠️ ' + safe(text), view=None)
        finally:
            self.stop()

    async def close(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.used = True
        self.stop()
        await interaction.response.edit_message(content='Closed. No entry was changed by this check.', view=None)


def render(draft):
    title = 'Ready' if draft['ready'] else 'Repair available' if draft['repairable'] else 'Needs review'
    lines = [f'# {draft["label"]} — {title}', '', *draft['checks'], '', safe(draft['reason'])]
    if draft['repairable']:
        lines += ['', 'Confirmation updates only the saved entry bindings. No content or access changes.']
    return '\n'.join(lines)


async def reject(interaction, kind, problem, *, deferred=False):
    if not deferred:
        await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    reference = f'ENTRY-{kind.upper()}-{problem}'
    log = entries.log
    log.warning('Ticket entry rejected guild=%s kind=%s check=%s', getattr(guild, 'id', None), kind, problem)
    view = None
    url = await entries.current_link(guild, interaction.user, kind) if guild else None
    if url:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Open current entry', url=url))
        content = 'This is an older entry. Open the verified current entry below.'
    else:
        content = f'This entry needs a staff check. Please contact the GamerHQ team. Reference: {reference}.'
    if guild and interaction.user.id == guild.owner_id:
        hint = (interaction.channel_id, interaction.message.id) if interaction.message else None
        view = EntryChecks(guild.id, interaction.user.id, kind, hint)
        if url:
            view.add_item(discord.ui.Button(label='Open current entry', url=url))
        content += '\nAs server owner, you can check this entry below. Nothing changes until you confirm a verified repair.'
    await interaction.followup.send(content, view=view, ephemeral=True,
                                    allowed_mentions=discord.AllowedMentions.none())
