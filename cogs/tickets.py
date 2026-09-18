"""Persistent support controls; every action checks current membership and scope."""
import logging
import discord
from discord.ext import commands
from database import db
from services import ticket_service as tickets
from services.response_service import SafeView, report_error

log = logging.getLogger(__name__)

async def reply_error(interaction, error):
    if isinstance(error, (ValueError, tickets.ServerMessageError)):
        await interaction.followup.send(f'⚠️ {error}', ephemeral=True)
    else:
        await report_error(interaction,error,'support ticket')

class TicketModal(discord.ui.Modal, title='Create Support Ticket'):
    subject = discord.ui.TextInput(label='Subject', max_length=100)
    description = discord.ui.TextInput(label='What do you need help with?', style=discord.TextStyle.paragraph, max_length=900)
    feature = discord.ui.TextInput(label='Feature (optional)', placeholder='Bot / LFG / Voice / Roles / Game Areas / Other', required=False, max_length=20)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            item, created = await tickets.open_ticket(interaction.guild, interaction.user, str(self.subject), str(self.description), str(self.feature))
            channel = interaction.guild.get_channel(item['channel_id']) if item['channel_id'] else None
            view = discord.ui.View()
            if channel:
                view.add_item(discord.ui.Button(label='Open Ticket' if created else 'Open Existing Ticket', url=f'https://discord.com/channels/{interaction.guild.id}/{channel.id}'))
            text = '✅ Your private support ticket is ready.' if created else '⚠️ You already have an open support ticket.'
            if not channel: text += ' Its creation needs staff review; no duplicate was created.'
            await interaction.followup.send(text,view=view,ephemeral=True)
        except Exception as error:
            await reply_error(interaction,error)

    async def on_error(self, interaction, error):
        await report_error(interaction,error,'ticket form')

class TicketEntry(SafeView):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label='Create Support Ticket', style=discord.ButtonStyle.primary, custom_id='gamerhq:tickets:create')
    async def create(self, interaction, button):
        raw = db.get_setting(f'ticket_entry:{interaction.guild_id}')
        channel = db.get_setting(f'managed_channel:{interaction.guild_id}:need-support')
        if not interaction.guild or str(interaction.message.id)!=raw or str(interaction.channel_id)!=channel:
            await interaction.response.send_message('⚠️ Open the current Need Support entry.',ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal())


def bound_ticket(interaction):
    with db.connect() as conn:
        row=conn.execute('SELECT id FROM support_tickets WHERE guild_id=? AND channel_id=? AND opening_message_id=?',(interaction.guild_id,interaction.channel_id,interaction.message.id)).fetchone()
    if not row: raise ValueError('Open the current ticket message to use its controls.')
    return tickets.get(row['id'])

class CloseConfirmation(SafeView):
    def __init__(self, actor_id, ticket_id):
        super().__init__(timeout=120)
        self.actor_id,self.ticket_id,self.used=actor_id,ticket_id,False

    @discord.ui.button(label='Confirm Close',style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        if interaction.user.id!=self.actor_id or self.used:
            await interaction.followup.send('⚠️ This confirmation is no longer available.',ephemeral=True)
            return
        self.used=True
        try:
            await tickets.change(interaction.guild,interaction.user,self.ticket_id,'close')
            await interaction.followup.send('✅ Ticket closed. You can still read its history.',ephemeral=True)
            self.stop()
        except Exception as error:
            self.used=False
            await reply_error(interaction,error)

    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if interaction.user.id!=self.actor_id:
            await interaction.response.send_message('⚠️ This confirmation belongs to another user.',ephemeral=True)
            return
        self.used=True
        self.stop()
        await interaction.response.edit_message(content='ℹ️ Ticket remains open.',view=None)

class TicketActions(SafeView):
    def __init__(self): super().__init__(timeout=None)

    async def act(self,interaction,action):
        await interaction.response.defer(ephemeral=True)
        try:
            item=bound_ticket(interaction)
            tickets.authorize(interaction.guild,interaction.user,item,staff_only=action!='close')
            if action=='close':
                if item['status']=='CLOSED': raise ValueError('This ticket is already closed.')
                await interaction.followup.send('Close this support ticket? Its history will be kept.',view=CloseConfirmation(interaction.user.id,item['id']),ephemeral=True)
            else:
                updated=await tickets.change(interaction.guild,interaction.user,item['id'],action)
                await interaction.followup.send(f'✅ Ticket status: {updated["status"]}.',ephemeral=True)
        except Exception as error:
            await reply_error(interaction,error)

    @discord.ui.button(label='Take Ticket',style=discord.ButtonStyle.primary,custom_id='gamerhq:tickets:take')
    async def take(self,interaction,button): await self.act(interaction,'take')

    @discord.ui.button(label='Waiting for User',style=discord.ButtonStyle.secondary,custom_id='gamerhq:tickets:wait')
    async def wait(self,interaction,button): await self.act(interaction,'wait')

    @discord.ui.button(label='Close Ticket',style=discord.ButtonStyle.danger,custom_id='gamerhq:tickets:close')
    async def close(self,interaction,button): await self.act(interaction,'close')

class SupportOffers(SafeView):
    """Persistent, canonical-board-bound routes into the existing ticket service."""
    def __init__(self, section=None):
        from services.support_service import TELESON_URL
        super().__init__(timeout=None)
        requests = list(self.children)
        self.clear_items()
        if section in (None, 'energy'):
            self.add_item(discord.ui.Button(label='⚡ Strom & Gas starten',style=discord.ButtonStyle.link,url=TELESON_URL))
        for button in requests:
            if section is None or (section == 'energy' and button.custom_id.endswith('energy-support')) or (section == 'energy_sales' and button.custom_id.endswith('energy-course')) or (section == 'finance' and button.custom_id.endswith('finance')):
                self.add_item(button)

    async def request(self, interaction, kind):
        from services import support_service as support
        await interaction.response.defer(ephemeral=True)
        try:
            guild = interaction.guild
            section = {'ENERGY_SUPPORT': 'energy', 'ENERGY_COURSE_REQUEST': 'energy_sales', 'FINANCE_REQUEST': 'finance'}.get(kind)
            if section is None:
                raise ValueError('Unbekannte Support-Anfrage.')
            if (not guild or str(interaction.channel_id)!=db.get_setting(support.channel_key(guild, support.section_channel(section)))
                    or str(interaction.message.id)!=db.get_setting(support.message_key(guild, section))):
                raise ValueError('Bitte nutze die aktuelle Nachricht in Germany Services.')
            title,description=tickets.REQUEST_COPY[kind]
            item,created=await tickets.open_ticket(guild,interaction.user,title,description,ticket_type=kind)
            channel=guild.get_channel(item['channel_id']) if item['channel_id'] else None
            view=discord.ui.View()
            if channel:
                view.add_item(discord.ui.Button(label='Anfrage öffnen',url=f'https://discord.com/channels/{guild.id}/{channel.id}'))
            text='✅ Deine private Anfrage wurde erstellt.' if created else 'ℹ️ Du hast bereits eine offene Anfrage dieser Art.'
            if not channel:text+=' Die Erstellung muss vom GamerHQ-Team geprüft werden. Es wurde keine doppelte Anfrage erstellt.'
            await interaction.followup.send(text,view=view,ephemeral=True)
        except Exception as error:
            await reply_error(interaction,error)

    @discord.ui.button(label='🆘 Support anfragen',style=discord.ButtonStyle.primary,custom_id='gamerhq:offers:energy-support')
    async def energy_support(self,interaction,button):await self.request(interaction,'ENERGY_SUPPORT')

    @discord.ui.button(label='🎓 Kurs anfragen',style=discord.ButtonStyle.secondary,custom_id='gamerhq:offers:energy-course')
    async def energy_course(self,interaction,button):await self.request(interaction,'ENERGY_COURSE_REQUEST')

    @discord.ui.button(label='💬 Finanzcheck anfragen',style=discord.ButtonStyle.primary,custom_id='gamerhq:offers:finance')
    async def finance(self,interaction,button):await self.request(interaction,'FINANCE_REQUEST')


class Tickets(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_load(self):
        self.bot.add_view(TicketEntry())
        self.bot.add_view(TicketActions())
        self.bot.add_view(SupportOffers())

    async def reconcile(self,guild,member_id=None):
        try:
            await tickets.recover(guild,member_id)
            if member_id is None: await tickets.refresh_entry(guild)
        except Exception:
            log.exception('Ticket recovery needs review guild=%s',guild.id)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds: await self.reconcile(guild)

    @commands.Cog.listener()
    async def on_member_remove(self,member): await self.reconcile(member.guild,member.id)

    @commands.Cog.listener()
    async def on_member_update(self,before,after):
        if before.roles!=after.roles: await self.reconcile(after.guild,after.id)

    @commands.Cog.listener()
    async def on_guild_role_update(self,before,after):
        if before.permissions!=after.permissions: await self.reconcile(after.guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self,role): await self.reconcile(role.guild)

    @commands.Cog.listener()
    async def on_member_join(self,member): await self.reconcile(member.guild,member.id)

async def setup(bot): await bot.add_cog(Tickets(bot))
