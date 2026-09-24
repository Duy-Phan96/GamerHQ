"""Ephemeral curated-deal draft, preview and explicit publish confirmation."""
from uuid import uuid4
import discord
from services import curated_deal_service as deals
from services.server_service import ServerMessageError


async def check_actor(interaction, guild_id, actor_id):
    try:
        if not interaction.guild or interaction.guild.id != guild_id or interaction.user.id != actor_id:
            raise ServerMessageError('This deal draft belongs to another session.')
        deals.target(interaction.guild, interaction.user)
        return True
    except ServerMessageError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return False


class DealModal(discord.ui.Modal, title='Create a Gaming Deal'):
    product = discord.ui.TextInput(label='Title / Product', max_length=200)
    current = discord.ui.TextInput(label='Current price (EUR, e.g. 99.99)', max_length=12)
    regular = discord.ui.TextInput(label='Regular price (EUR, optional)', max_length=12, required=False)
    url = discord.ui.TextInput(label='Verified affiliate / product HTTPS URL', max_length=512)
    note = discord.ui.TextInput(label='Short note (optional)', style=discord.TextStyle.paragraph, max_length=500, required=False)

    def __init__(self, guild_id, actor_id, partner, image_url='', *, deal=None, draft_id=None):
        super().__init__(timeout=300)
        self.guild_id, self.actor_id, self.partner = guild_id, actor_id, partner
        self.image_url, self.draft_id = image_url, draft_id or uuid4().hex
        if deal:
            for field, value in ((self.product, deal.title), (self.current, deal.current_price),
                                 (self.regular, deal.regular_price), (self.url, deal.url), (self.note, deal.note)):
                field.default = value

    async def on_submit(self, interaction):
        if not await check_actor(interaction, self.guild_id, self.actor_id):
            return
        try:
            deal = deals.validate(deals.Deal(self.partner, str(self.product), str(self.current),
                str(self.regular), str(self.url), str(self.note), self.image_url))
            channel = deals.target(interaction.guild, interaction.user)
            payload = deals.render(deal)
        except (ServerMessageError, ValueError):
            return await interaction.response.send_message(
                'Invalid deal. Check partner URL, title and EUR prices (e.g. 99.99; regular price must be positive and ≥ current). Run /deals create again to correct it.', ephemeral=True)
        payload['view'] = DealPreview(self.guild_id, self.actor_id, channel.id, self.draft_id, deal)
        await interaction.response.send_message(
            content=f'**Preview — not posted** · Target: {channel.mention}\nVerify the product, prices and partner link before posting.',
            **payload, ephemeral=True)


class DealPreview(discord.ui.View):
    def __init__(self, guild_id, actor_id, channel_id, draft_id, deal):
        super().__init__(timeout=300)
        self.guild_id, self.actor_id, self.channel_id = guild_id, actor_id, channel_id
        self.draft_id, self.deal, self.used = draft_id, deal, False
        _, emoji, label = deals.PARTNERS[deal.partner]
        self.add_item(discord.ui.Button(label=label, emoji=emoji, url=deal.url))

    async def interaction_check(self, interaction):
        return await check_actor(interaction, self.guild_id, self.actor_id)

    async def take(self, interaction):
        if not await self.interaction_check(interaction):
            return False
        if self.used:
            await interaction.response.send_message('This draft step has already been used.', ephemeral=True)
            return False
        self.used = True
        self.stop()
        return True

    @discord.ui.button(label='Post Deal', style=discord.ButtonStyle.success)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.take(interaction):
            return
        await interaction.response.defer()
        try:
            result = await deals.publish(interaction.guild, interaction.user, self.draft_id, self.channel_id, self.deal)
            text = {'posted': 'Deal posted in gaming-deals.', 'retained': 'This draft already has a delivery record; no duplicate sent.',
                    'uncertain': 'Delivery uncertain. Check gaming-deals before creating another draft; this draft will not be retried.'}[result]
        except (ServerMessageError, ValueError) as exc:
            text = str(exc)
        await interaction.edit_original_response(content=text, embed=None, view=None)

    @discord.ui.button(label='Edit', style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.take(interaction):
            return
        await interaction.response.send_modal(DealModal(self.guild_id, self.actor_id, self.deal.partner,
            self.deal.image_url, deal=self.deal, draft_id=self.draft_id))

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.take(interaction):
            await interaction.response.edit_message(content='Deal cancelled. Nothing posted.', embed=None, view=None)
