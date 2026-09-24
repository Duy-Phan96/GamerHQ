"""Manual GoCDKeys paste → private preview → confirmed import."""
from dataclasses import replace
import discord
from cogs.deal_editor import check_actor
from services import gocdkeys_import_service as imports
from services.server_service import ServerMessageError


def preview_embed(plan, page):
    entries = plan.entries
    counts = {state: sum(e.state == state for e in entries) for state in ('new', 'posted', 'retained', 'duplicate', 'invalid')}
    embed = discord.Embed(title='🎮 GoCDKeys Import Preview', description=(
        f'New deals: {counts["new"]}\nAlready posted: {counts["posted"]}\n'
        f'Retained delivery claims: {counts["retained"]}\nDuplicates in batch: {counts["duplicate"]}\nInvalid/manual review: {counts["invalid"]}\n'
        f'Target: <#{plan.channel_id}>\nNothing posted. Review titles and links; missing titles must be edited.'), color=0x5865F2)
    for entry in entries[page * 3:page * 3 + 3]:
        text = entry.reason if entry.state == 'invalid' else f'{entry.state}\n[Preview URL](<{entry.url}>)'
        if entry.note:
            text += '\n' + discord.utils.escape_markdown(entry.note)
        embed.add_field(name=f'{entry.line}. {entry.title or "Title required"}'[:240], value=text, inline=False)
    embed.set_footer(text=f'Page {page+1}/{(len(entries)+2)//3} · No page fetches or automatic price claims')
    return embed


async def send_preview(interaction, plan, warning=None):
    view = ImportPreview(interaction.guild.id, interaction.user.id, plan)
    await interaction.response.send_message(content=warning, embed=preview_embed(plan, 0), view=view, ephemeral=True,
                                           allowed_mentions=discord.AllowedMentions.none())


class ImportModal(discord.ui.Modal, title='Import GoCDKeys partner links'):
    links = discord.ui.TextInput(label='1–10 partner links, one per line', style=discord.TextStyle.paragraph, max_length=4000)

    def __init__(self, guild_id, actor_id):
        super().__init__(timeout=300)
        self.guild_id, self.actor_id = guild_id, actor_id

    async def on_submit(self, interaction):
        if not await check_actor(interaction, self.guild_id, self.actor_id):
            return
        try:
            plan = imports.preview(interaction.guild, interaction.user, str(self.links))
        except ServerMessageError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await send_preview(interaction, plan)


class TitlesModal(discord.ui.Modal, title='Edit imported deal titles'):
    titles = discord.ui.TextInput(label='Line number | title (each new link)', style=discord.TextStyle.paragraph, max_length=2200)
    notes = discord.ui.TextInput(label='Optional: line number | short note', style=discord.TextStyle.paragraph, max_length=1500, required=False)

    def __init__(self, guild_id, actor_id, plan):
        super().__init__(timeout=300)
        self.guild_id, self.actor_id, self.plan = guild_id, actor_id, plan
        self.titles.default = '\n'.join(f'{e.line} | {e.title}' for e in plan.entries if e.state == 'new')
        self.notes.default = '\n'.join(f'{e.line} | {e.note}' for e in plan.entries if e.state == 'new' and e.note)

    def parse(self, text, limit):
        result = {}
        wanted = {e.line for e in self.plan.entries if e.state == 'new'}
        for line in text.splitlines():
            if not line.strip():
                continue
            number, sep, value = line.partition('|')
            if not sep or not number.strip().isdigit() or int(number) not in wanted or int(number) in result or not 1 <= len(value.strip()) <= limit:
                raise ValueError()
            result[int(number)] = value.strip()
        return result

    async def on_submit(self, interaction):
        if not await check_actor(interaction, self.guild_id, self.actor_id):
            return
        try:
            titles, notes = self.parse(str(self.titles), 200), self.parse(str(self.notes), 120)
            if set(titles) != {e.line for e in self.plan.entries if e.state == 'new'}:
                raise ValueError()
        except ValueError:
            return await send_preview(interaction, self.plan, 'Edits rejected. Use one “line number | title” per new link (1–200 characters); optional notes max 120 characters. Try Edit Titles again.')
        plan = replace(self.plan, entries=tuple(replace(e, title=titles[e.line], note=notes.get(e.line, ''))
                       if e.state == 'new' else e for e in self.plan.entries))
        await send_preview(interaction, plan)


class ImportPreview(discord.ui.View):
    def __init__(self, guild_id, actor_id, plan):
        super().__init__(timeout=300)
        self.guild_id, self.actor_id, self.plan, self.page, self.used = guild_id, actor_id, plan, 0, False
        candidates = [e for e in plan.entries if e.state == 'new']
        self.post.disabled = not candidates or any(not e.title for e in candidates)
        self.edit.disabled = not candidates
        self.update_navigation()

    def update_navigation(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * 3 >= len(self.plan.entries)

    async def interaction_check(self, interaction):
        return await check_actor(interaction, self.guild_id, self.actor_id)

    async def active(self, interaction, *, consume=False):
        if not await self.interaction_check(interaction):
            return False
        if self.used:
            await interaction.response.send_message('This preview has already been used.', ephemeral=True)
            return False
        if consume:
            self.used = True
            self.stop()
        return True

    @discord.ui.button(label='Post New Deals', style=discord.ButtonStyle.success)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.active(interaction, consume=True):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content='Importing confirmed new links…', embed=None, view=None)
        try:
            results = await imports.publish(interaction.guild, interaction.user, self.plan)
            labels = {'created': '✅ Posted', 'posted': 'ℹ️ Already posted', 'retained': 'ℹ️ Existing delivery claim',
                      'duplicate': 'ℹ️ Duplicate in batch', 'invalid': '⚠️ Invalid/manual review', 'uncertain': '⚠️ Delivery uncertain'}
            text = '\n'.join(f'{labels[state]}: {discord.utils.escape_markdown(e.title) or "Link " + str(e.line)}' for e, state in results)
            embed = discord.Embed(title='GoCDKeys Import Complete', description=text,
                                  color=0x5865F2)
            embed.set_footer(text='Uncertain/reserved deliveries are never retried automatically. Inspect Discord before a new attempt.')
            await interaction.edit_original_response(content=None, embed=embed, view=None)
        except ServerMessageError as exc:
            await interaction.edit_original_response(content=f'Import stopped: {exc} Any completed delivery records are retained; create a new preview.', view=None)

    @discord.ui.button(label='Edit Titles', style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.active(interaction, consume=True):
            await interaction.response.send_modal(TitlesModal(self.guild_id, self.actor_id, self.plan))

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.active(interaction, consume=True):
            await interaction.response.edit_message(content='Import cancelled. Nothing posted.', embed=None, view=None)

    @discord.ui.button(label='Previous', row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.active(interaction):
            self.page = max(0, self.page - 1)
            self.update_navigation()
            await interaction.response.edit_message(embed=preview_embed(self.plan, self.page), view=self)

    @discord.ui.button(label='Next', row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.active(interaction):
            self.page = min((len(self.plan.entries)-1)//3, self.page + 1)
            self.update_navigation()
            await interaction.response.edit_message(embed=preview_embed(self.plan, self.page), view=self)
