"""Persistent support controls; every action checks current membership and scope."""
import logging
import discord
from discord import app_commands
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

async def reply_opened_ticket(interaction, item, created, *, request=False):
    """Link the saved private chat; an uncertain creation is not a ready ticket."""
    guild = interaction.guild
    channel_id = item.get('channel_id')
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None and channel_id:
        try:
            channel = await guild.fetch_channel(channel_id)
        except (discord.HTTPException, TimeoutError):
            channel = None
    ready = bool(channel and channel.guild.id == guild.id and item.get('opening_message_id'))
    if ready:
        access = channel.permissions_for(interaction.user)
        ready = bool(access.view_channel and access.read_message_history)
    view = None
    if ready:
        view = discord.ui.View()
        label = ('Open Request' if created else 'Open Existing Request') if request else (
            'Open Ticket' if created else 'Open Existing Ticket')
        view.add_item(discord.ui.Button(label=label,
            url=f'https://discord.com/channels/{guild.id}/{channel.id}'))
        text = ('✅ Your private request has been created.' if created else
                'ℹ️ You already have an open request of this type.') if request else (
                '✅ Your private support chat is ready. Tell us what you need help with in the ticket.' if created else
                'ℹ️ You already have an open support ticket. Continue in your existing chat.')
    else:
        text = ('Your request is saved, but its chat is not confirmed ready. Please ask the GamerHQ team '
                'to check it. No replacement was created.')
    await interaction.followup.send(text, view=view, ephemeral=True,
                                    allowed_mentions=discord.AllowedMentions.none())


class TicketModal(discord.ui.Modal, title='Create Support Ticket'):
    """Compatibility for previously opened forms; the public button no longer requires one."""
    subject = discord.ui.TextInput(label='Subject', max_length=100)
    description = discord.ui.TextInput(label='What do you need help with?', style=discord.TextStyle.paragraph, max_length=900)
    feature = discord.ui.TextInput(label='Feature (optional)', placeholder='Bot / LFG / Voice / Roles / Game Areas / Other', required=False, max_length=20)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            item, created = await tickets.open_ticket(interaction.guild, interaction.user, str(self.subject), str(self.description), str(self.feature))
            await reply_opened_ticket(interaction, item, created)
        except Exception as error:
            await reply_error(interaction,error)

    async def on_error(self, interaction, error):
        await report_error(interaction,error,'ticket form')

class TicketEntry(SafeView):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label='Create Support Ticket', style=discord.ButtonStyle.primary, custom_id='gamerhq:tickets:create')
    async def create(self, interaction, button):
        from services.ticket_entry_service import binding_problem
        from cogs.ticket_entry_repair import reject
        await interaction.response.defer(ephemeral=True)
        try:
            problem = binding_problem(interaction, 'support')
            if problem:
                await reject(interaction, 'support', problem, deferred=True)
                return
            item, created = await tickets.open_support_chat(interaction.guild, interaction.user)
            await reply_opened_ticket(interaction, item, created)
        except Exception as error:
            await reply_error(interaction, error)


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
    def __init__(self, *, status=None):
        super().__init__(timeout=None)
        if status == 'CLOSED':
            self.clear_items()  # Old persistent callbacks still check the stored closed state.
        elif status is not None:
            self.wait.disabled = status != 'IN_PROGRESS'

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
        super().__init__(timeout=None)
        if section not in (None, 'household'):
            self.clear_items()

    async def request(self, interaction, kind):
        from services import support_service as support
        await interaction.response.defer(ephemeral=True)
        try:
            guild = interaction.guild
            section = {'ELECTRICITY_REQUEST': 'household'}.get(kind)
            if section is None:
                raise ValueError('Please use the current Electricity request button.')
            from services.ticket_entry_service import binding_problem
            from cogs.ticket_entry_repair import reject
            problem = binding_problem(interaction, 'electricity')
            if problem:
                await reject(interaction, 'electricity', problem, deferred=True)
                return
            title,description=tickets.REQUEST_COPY[kind]
            item,created=await tickets.open_ticket(guild,interaction.user,title,description,ticket_type=kind)
            await reply_opened_ticket(interaction, item, created, request=True)
        except Exception as error:
            await reply_error(interaction,error)

    @discord.ui.button(label='⚡ Compare Electricity Tariffs', style=discord.ButtonStyle.primary,
                       custom_id='gamerhq:offers:electricity')
    async def electricity(self, interaction, button):
        await self.request(interaction, 'ELECTRICITY_REQUEST')


def _current_staff(guild, user):
    member = guild.get_member(user.id) if guild and user else None
    return member if member and tickets.staff(member) else None


def _overview_text(guild, rows):
    counts = {
        'OPEN': sum(row['status'] == 'OPEN' for row in rows),
        'IN_PROGRESS': sum(row['status'] == 'IN_PROGRESS' for row in rows),
        'WAITING_FOR_USER': sum(row['status'] == 'WAITING_FOR_USER' for row in rows),
    }
    lines = [
        '# 🎫 Ticket Overview',
        'Read-only staff view of active support tickets.',
        '',
        f"Open / unassigned: **{counts['OPEN']}** · In progress: **{counts['IN_PROGRESS']}** · Waiting: **{counts['WAITING_FOR_USER']}**",
    ]
    if not rows:
        lines += ['', 'No active tickets.']
        return '\n'.join(lines)
    lines += ['', 'Select a ticket below to review its current assignment and open the private channel.']
    if len(rows) > 25:
        lines += [f'{len(rows) - 25} additional active tickets are not shown in this selector.']
    return '\n'.join(lines)


def _ticket_detail(guild, row):
    type_label = tickets.TICKET_TYPES.get(row['ticket_type'], 'Support Ticket')
    assigned = discord.utils.escape_markdown(row['assigned_name']) if row['assigned_name'] else 'Unassigned'
    creator = discord.utils.escape_markdown(row['creator_name']) if row['creator_name'] else 'Left / unavailable'
    channel = f"<#{row['channel_id']}>" if row['channel_ready'] else 'Channel unavailable — staff review required'
    status = {
        'OPEN': 'Open',
        'IN_PROGRESS': 'In progress',
        'WAITING_FOR_USER': 'Waiting for user',
    }.get(row['status'], row['status'])
    return (
        f"# 🎫 Ticket #{row['id']:04d}\n"
        f"**Type:** {discord.utils.escape_markdown(type_label)}\n"
        f"**Status:** {status}\n"
        f"**Assigned to:** {assigned}\n"
        f"**Creator:** {creator}\n"
        f"**Channel:** {channel}\n\n"
        "This view is read-only. Use the controls inside the private ticket to take, wait or close it."
    )


class TicketOverviewSelect(discord.ui.Select):
    def __init__(self, rows):
        self.rows = {str(row['id']): row for row in rows[:25]}
        options = []
        for row in rows[:25]:
            assigned = row['assigned_name'] or 'Unassigned'
            status = {
                'OPEN': 'Open',
                'IN_PROGRESS': 'In progress',
                'WAITING_FOR_USER': 'Waiting',
            }.get(row['status'], row['status'])
            options.append(discord.SelectOption(
                label=f"Ticket #{row['id']:04d} · {status}"[:100],
                description=f"{assigned} · {tickets.TICKET_TYPES.get(row['ticket_type'], 'Support')}"[:100],
                value=str(row['id']),
            ))
        super().__init__(placeholder='Select active ticket', min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        view = self.view
        if not await view.authorized(interaction):
            return
        item = tickets.get(int(self.values[0]))
        if not item or item['guild_id'] != view.guild_id or item['status'] == 'CLOSED':
            return await interaction.response.edit_message(
                content='This ticket changed or closed. Refresh the overview.',
                view=TicketOverviewView(interaction.guild, interaction.user.id),
            )
        current = next((row for row in tickets.active_overview(interaction.guild) if row['id'] == item['id']), None)
        if current is None:
            return await interaction.response.edit_message(
                content='This ticket is no longer active. Refresh the overview.',
                view=TicketOverviewView(interaction.guild, interaction.user.id),
            )
        detail_view = TicketOverviewDetailView(interaction.guild, interaction.user.id, current)
        await interaction.response.edit_message(
            content=_ticket_detail(interaction.guild, current),
            view=detail_view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class TicketOverviewView(SafeView):
    def __init__(self, guild, actor_id):
        super().__init__(timeout=180)
        self.guild_id, self.actor_id = guild.id, actor_id
        rows = tickets.active_overview(guild)
        if rows:
            self.add_item(TicketOverviewSelect(rows))

    async def authorized(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id or interaction.user.id != self.actor_id:
            await interaction.response.send_message('This staff view belongs to another session.', ephemeral=True)
            return False
        if _current_staff(interaction.guild, interaction.user) is None:
            await interaction.response.send_message('Staff access is no longer available.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Refresh', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        if not await self.authorized(interaction):
            return
        rows = tickets.active_overview(interaction.guild)
        await interaction.response.edit_message(
            content=_overview_text(interaction.guild, rows),
            view=TicketOverviewView(interaction.guild, interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label='Close', style=discord.ButtonStyle.secondary)
    async def close(self, interaction, button):
        if not await self.authorized(interaction):
            return
        await interaction.response.edit_message(content='Ticket overview closed.', view=None)
        self.stop()


class TicketOverviewDetailView(TicketOverviewView):
    def __init__(self, guild, actor_id, row):
        SafeView.__init__(self, timeout=180)
        self.guild_id, self.actor_id = guild.id, actor_id
        if row['channel_ready']:
            self.add_item(discord.ui.Button(
                label='Open Ticket',
                url=f"https://discord.com/channels/{guild.id}/{row['channel_id']}",
            ))

    @discord.ui.button(label='Back to Overview', style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if not await self.authorized(interaction):
            return
        rows = tickets.active_overview(interaction.guild)
        await interaction.response.edit_message(
            content=_overview_text(interaction.guild, rows),
            view=TicketOverviewView(interaction.guild, interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class Tickets(commands.Cog):
    ticket_tools = app_commands.Group(name='tickets', description='Support ticket tools for GamerHQ staff.')

    def __init__(self,bot): self.bot=bot

    @ticket_tools.command(name='overview', description='Show active support tickets and staff assignments.')
    async def overview(self, interaction: discord.Interaction):
        member = _current_staff(interaction.guild, interaction.user)
        if member is None:
            return await interaction.response.send_message('Staff only.', ephemeral=True)
        rows = tickets.active_overview(interaction.guild)
        await interaction.response.send_message(
            _overview_text(interaction.guild, rows),
            view=TicketOverviewView(interaction.guild, interaction.user.id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    async def cog_load(self):
        self.bot.add_view(TicketEntry())
        self.bot.add_view(TicketActions())
        self.bot.add_view(SupportOffers())

    async def reconcile(self,guild,member_id=None):
        try:
            await tickets.recover(guild,member_id)
        except Exception:
            log.exception('Ticket recovery needs review guild=%s',guild.id)
        if member_id is None:
            # A private ticket failure must not prevent healthy public entries
            # from refreshing. Each phase has independent errors and no setup.
            from services.support_service import refresh_electricity_entry
            for kind, refresh in [('support', tickets.refresh_entry), ('electricity', refresh_electricity_entry)]:
                try:
                    await refresh(guild)
                except Exception:
                    log.exception('Ticket entry refresh needs review guild=%s kind=%s',guild.id,kind)

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
