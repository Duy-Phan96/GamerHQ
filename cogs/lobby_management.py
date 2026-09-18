"""Compact host/member panels on the existing LFG event model."""
from datetime import datetime

import discord

from database import db
from services import lobby_service as rules
from services.lfg_service import SERVER_TZ, parse_server_datetime, render_event, member_has_game_role


def authorized_event(interaction, event_id, *, host=False):
    event = db.get_lfg_event(event_id)
    if not interaction.guild or not event or event['guild_id'] != interaction.guild.id or event['status'] != 'scheduled':
        raise ValueError('This lobby is no longer available here.')
    if host and event['host_id'] != interaction.user.id:
        raise ValueError('Only the host can manage this lobby.')
    if not host and interaction.user.id not in [r['user_id'] for r in db.get_lfg_event_members(event_id) if r['status'] == 'joined']:
        raise ValueError('Join the lobby to use these actions.')
    return event


async def error(interaction, exc):
    if interaction.response.is_done():
        await interaction.followup.send(str(exc), ephemeral=True)
    else:
        await interaction.response.send_message(str(exc), ephemeral=True)


async def notify_members(guild, event, text):
    for row in db.get_lfg_event_members(event['id']):
        if row['status'] != 'joined':
            continue
        member = guild.get_member(row['user_id'])
        if member and not member.bot:
            try:
                await member.send(f"🎮 **{event['title']}**\n{text}", allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass


async def changed(guild, event, *, time_changed=False):
    from cogs.lfg import refresh_event_posts
    await refresh_event_posts(guild, event['id'])
    if time_changed:
        await notify_members(guild, event, f"The host changed the start to <t:{event['start_at']}:F>. Voice reminder: {event['invite_lead_minutes']} minutes before.")


async def revoke_access(guild, event, user_id):
    from cogs.lfg import _revoke_private_event_access
    member = guild.get_member(user_id)
    if not member:
        return
    await _revoke_private_event_access(guild, event, member)
    voice = guild.get_channel(event['voice_channel_id']) if event.get('voice_channel_id') else None
    if isinstance(voice, discord.VoiceChannel):
        await voice.set_permissions(member, overwrite=None)
        if member.voice and member.voice.channel and member.voice.channel.id == voice.id:
            await member.move_to(None, reason='Left or removed from GamerHQ lobby')


class EditModal(discord.ui.Modal):
    def __init__(self, event, schedule=False):
        super().__init__(title='Change Date / Time' if schedule else 'Edit Lobby Details')
        self.event_id = event['id']
        self.schedule = schedule
        if schedule:
            local = datetime.fromtimestamp(event['start_at'], SERVER_TZ)
            self.date = discord.ui.TextInput(label='Date (YYYY-MM-DD, Europe/Berlin)', default=local.strftime('%Y-%m-%d'), max_length=10)
            self.time = discord.ui.TextInput(label='Time (HH:MM, Europe/Berlin)', default=local.strftime('%H:%M'), max_length=5)
            self.add_item(self.date); self.add_item(self.time)
        else:
            self.name = discord.ui.TextInput(label='Title', default=event['title'], max_length=100)
            self.note = discord.ui.TextInput(label='Note / description', default=event.get('note', ''), required=False, max_length=500, style=discord.TextStyle.paragraph)
            self.seats = discord.ui.TextInput(label='Seats including host (1–99)', default=str(event['max_players']), max_length=2)
            self.reminder = discord.ui.TextInput(label='Voice reminder: minutes before (0–1440)', default=str(event['invite_lead_minutes']), max_length=4)
            for item in (self.name, self.note, self.seats, self.reminder): self.add_item(item)

    async def on_submit(self, interaction):
        try:
            old = authorized_event(interaction, self.event_id, host=True)
            values = {'start_at': parse_server_datetime(str(self.date), str(self.time))} if self.schedule else {
                'title': str(self.name), 'note': str(self.note), 'max_players': int(str(self.seats)), 'invite_lead_minutes': int(str(self.reminder))}
            await interaction.response.defer(ephemeral=True)
            event = rules.edit(self.event_id, interaction.guild.id, interaction.user.id, **values)
            await changed(interaction.guild, event, time_changed=event['start_at'] != old['start_at'])
            await interaction.edit_original_response(content='✅ Lobby updated.', view=LobbyPanel(event, interaction.user.id))
        except ValueError as exc:
            await error(interaction, exc)


class SuggestModal(discord.ui.Modal, title='Suggest New Time'):
    date = discord.ui.TextInput(label='Date (YYYY-MM-DD, Europe/Berlin)', max_length=10)
    time = discord.ui.TextInput(label='Time (HH:MM, Europe/Berlin)', max_length=5)
    reason = discord.ui.TextInput(label='Reason (optional)', required=False, max_length=300)

    def __init__(self, event_id):
        super().__init__()
        self.event_id = event_id

    async def on_submit(self, interaction):
        try:
            event = authorized_event(interaction, self.event_id)
            start_at = parse_server_datetime(str(self.date), str(self.time))
            proposal_id = rules.propose(self.event_id, interaction.guild.id, interaction.user.id, start_at, str(self.reason))
            await interaction.response.defer(ephemeral=True)
            await changed(interaction.guild, event)
            host = interaction.guild.get_member(event['host_id'])
            if host:
                try:
                    await host.send(f"🕒 **{event['title']}**: <@{interaction.user.id}> suggests <t:{start_at}:F>\nCurrent: <t:{event['start_at']}:F>\n{str(self.reason)}\nReview below or use **Lobby Actions → Time Proposals** in GamerHQ.", view=ProposalDMView(event['id'], proposal_id), allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    pass
            await interaction.edit_original_response(content=f'✅ Proposal #{proposal_id} saved. The host has the final decision.', view=None)
        except ValueError as exc:
            await error(interaction, exc)


class ProposalDMView(discord.ui.View):
    """Persistent host decision buttons; SQLite rechecks owner and pending state."""
    def __init__(self, event_id, proposal_id):
        super().__init__(timeout=None)
        for label, decision in [('Accept', 'ACCEPTED'), ('Decline', 'DECLINED')]:
            button = discord.ui.Button(label=label, custom_id=f'gamerhq:lfg:proposal:{proposal_id}:{decision}')
            async def callback(interaction, decision=decision):
                try:
                    event = db.get_lfg_event(event_id)
                    guild = interaction.client.get_guild(event['guild_id']) if event else None
                    if not guild or not guild.get_member(interaction.user.id):
                        raise ValueError('This lobby is no longer available to you.')
                    event = rules.decide(event_id, guild.id, interaction.user.id, proposal_id, decision)
                    await interaction.response.defer()
                    await changed(guild, event, time_changed=decision == 'ACCEPTED')
                    await interaction.edit_original_response(content=f'Proposal {decision.lower()}.', view=None)
                except ValueError as exc:
                    await error(interaction, exc)
            button.callback = callback
            self.add_item(button)


class PlayerSelect(discord.ui.UserSelect):
    def __init__(self, event_id, remove=False):
        super().__init__(placeholder='Choose participant to remove' if remove else 'Choose a player to invite', max_values=1)
        self.event_id, self.remove = event_id, remove

    async def callback(self, interaction):
        try:
            event = authorized_event(interaction, self.event_id, host=True)
            member = interaction.guild.get_member(self.values[0].id)
            if not member or member.bot:
                raise ValueError('Choose a human member of this server.')
            if self.remove:
                return await interaction.response.edit_message(content=f'Remove <@{member.id}> from this lobby?', view=RemoveConfirm(event['id'], interaction.user.id, member.id))
            await interaction.response.defer(ephemeral=True)
            if not rules.invite(event['id'], interaction.guild.id, interaction.user.id, member.id):
                return await interaction.edit_original_response(content='Already invited or joined; no duplicate invitation sent.', view=LobbyPanel(event, interaction.user.id))
            from cogs.lfg import LFGInviteDMView, _grant_private_event_access
            if event['visibility'] == 'private':
                await _grant_private_event_access(interaction.guild, event, member)
            game = db.get_game_by_id(event['game_id'])
            count = sum(r['status'] == 'joined' for r in db.get_lfg_event_members(event['id']))
            delivered = True
            try:
                await member.send(f"🎮 **{event['title']}** · {game['name'] if game else 'Gaming'}\nHost: <@{event['host_id']}>\n<t:{event['start_at']}:F>\nOpen seats: {max(0, event['max_players'] - count)}\nJoin when a seat is available.", view=LFGInviteDMView(event['id'], interaction.guild.id), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                delivered = False
            warning = '\n⚠️ This player does not have the game role; Add Game & Join remains available.' if game and not member_has_game_role(member, game) else ''
            await changed(interaction.guild, event)
            await interaction.edit_original_response(content=('✅ Invitation sent.' if delivered else 'Invitation saved, but DM delivery failed. Share the lobby with this player.') + warning, view=LobbyPanel(event, interaction.user.id))
        except ValueError as exc:
            await error(interaction, exc)


class BackView(discord.ui.View):
    def __init__(self, event_id, user_id):
        super().__init__(timeout=300)
        self.event_id, self.user_id = event_id, user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await error(interaction, 'This panel belongs to another user.')
            return False
        return True

    @discord.ui.button(label='Back', row=4)
    async def back(self, interaction, button):
        await open_panel(interaction, self.event_id, update=True)

    @discord.ui.button(label='Dismiss', row=4)
    async def dismiss(self, interaction, button):
        await interaction.response.edit_message(content='Panel closed.', view=None)


class RemoveConfirm(BackView):
    def __init__(self, event_id, user_id, target_id):
        super().__init__(event_id, user_id)
        self.target_id = target_id

    @discord.ui.button(label='Remove Player', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        try:
            event = authorized_event(interaction, self.event_id, host=True)
            rules.remove(event['id'], interaction.guild.id, interaction.user.id, self.target_id)
            await interaction.response.defer(ephemeral=True)
            access_ok = True
            try:
                await revoke_access(interaction.guild, event, self.target_id)
            except discord.HTTPException:
                access_ok = False
            await changed(interaction.guild, event)
            await interaction.edit_original_response(content='Player removed.' + ('' if access_ok else ' Discord access cleanup failed; ask staff to remove the channel override.'), view=LobbyPanel(event, interaction.user.id))
        except ValueError as exc:
            await error(interaction, exc)


class ProposalSelect(discord.ui.Select):
    def __init__(self, rows):
        super().__init__(placeholder='Review a time proposal', options=[discord.SelectOption(label=f"#{r['id']} · {datetime.fromtimestamp(r['start_at'], SERVER_TZ):%d %b %H:%M}", value=str(r['id'])) for r in rows])

    async def callback(self, interaction):
        try:
            event = authorized_event(interaction, self.view.event_id)
            row = next((r for r in rules.proposals(event['id']) if r['id'] == int(self.values[0])), None)
            if not row:
                raise ValueError('This proposal is no longer pending.')
            await interaction.response.edit_message(content=f"Current: <t:{event['start_at']}:F>\nSuggested: <t:{row['start_at']}:F>\nBy <@{row['proposer_id']}>\n{discord.utils.escape_markdown(row['reason'])}\nHost decision: PENDING", view=ProposalDecision(event, interaction.user.id, row))
        except ValueError as exc:
            await error(interaction, exc)


class ProposalDecision(BackView):
    def __init__(self, event, user_id, proposal):
        super().__init__(event['id'], user_id)
        for label, decision in [('Accept', 'ACCEPTED'), ('Decline', 'DECLINED'), ('Withdraw', 'WITHDRAWN')]:
            allowed = user_id == (proposal['proposer_id'] if decision == 'WITHDRAWN' else event['host_id'])
            if not allowed:
                continue
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            async def callback(interaction, decision=decision):
                try:
                    authorized_event(interaction, self.event_id)
                    event = rules.decide(self.event_id, interaction.guild.id, interaction.user.id, proposal['id'], decision)
                    await interaction.response.defer(ephemeral=True)
                    await changed(interaction.guild, event, time_changed=decision == 'ACCEPTED')
                    await interaction.edit_original_response(content=f'Proposal {decision.lower()}.', view=LobbyPanel(event, interaction.user.id))
                except ValueError as exc:
                    await error(interaction, exc)
            button.callback = callback
            self.add_item(button)


class EndConfirm(BackView):
    def __init__(self, event_id, user_id, status):
        super().__init__(event_id, user_id)
        self.status = status

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        try:
            authorized_event(interaction, self.event_id, host=True)
            event = rules.end(self.event_id, interaction.guild.id, interaction.user.id, self.status)
            await interaction.response.defer(ephemeral=True)
            await changed(interaction.guild, event)
            await notify_members(interaction.guild, event, f"Lobby {self.status}. Resources are cleaned up once voice is empty; the final card remains for 24 hours.")
            await interaction.edit_original_response(content=f'Lobby {self.status}.', view=None)
        except ValueError as exc:
            await error(interaction, exc)


class ActionSelect(discord.ui.Select):
    def __init__(self, event, user_id):
        labels = ['View Participants', 'Time Proposals', 'Suggest New Time', 'Open / Join Voice']
        if user_id == event['host_id']:
            labels += ['Edit Details / Seats / Reminder', 'Change Date / Time', 'Invite Player', 'Remove Player', 'Share Lobby', 'Regenerate Private Invite', 'Close Lobby', 'Cancel Lobby']
        else:
            labels += ['Leave Lobby']
        super().__init__(placeholder='Choose lobby action', options=[discord.SelectOption(label=s, value=s) for s in labels])

    async def callback(self, interaction):
        try:
            action = self.values[0]
            host_action = action in {'Edit Details / Seats / Reminder', 'Change Date / Time', 'Invite Player', 'Remove Player', 'Share Lobby', 'Regenerate Private Invite', 'Close Lobby', 'Cancel Lobby'}
            event = authorized_event(interaction, self.view.event_id, host=host_action)
            if action.startswith('Edit Details') or action == 'Change Date / Time':
                return await interaction.response.send_modal(EditModal(event, schedule=action == 'Change Date / Time'))
            if action == 'Suggest New Time':
                return await interaction.response.send_modal(SuggestModal(event['id']))
            if action in {'Invite Player', 'Remove Player'}:
                view = BackView(event['id'], interaction.user.id)
                view.add_item(PlayerSelect(event['id'], remove=action == 'Remove Player'))
                return await interaction.response.edit_message(content=action, view=view)
            if action == 'Time Proposals':
                rows = rules.proposals(event['id'])
                view = BackView(event['id'], interaction.user.id)
                if rows: view.add_item(ProposalSelect(rows))
                return await interaction.response.edit_message(content='Choose a suggestion. Only the host may accept or decline.' if rows else 'No pending time proposals.', view=view)
            if action == 'View Participants':
                rows = [r for r in db.get_lfg_event_members(event['id']) if r['status'] == 'joined']
                # Embeds allow the complete 99-seat roster without exceeding message limits.
                embed = discord.Embed(title='Lobby Participants', description='\n'.join(f"<@{r['user_id']}>" for r in rows))
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            if action in {'Close Lobby', 'Cancel Lobby'}:
                return await interaction.response.edit_message(content=f"{action}? This ends the lobby for everyone and cannot be undone.", view=EndConfirm(event['id'], interaction.user.id, 'completed' if action == 'Close Lobby' else 'cancelled'))
            if action == 'Share Lobby':
                from cogs.lfg import LFGEventView
                return await LFGEventView(event['id']).share_event(interaction)
            if action == 'Regenerate Private Invite':
                if event['visibility'] != 'private':
                    raise ValueError('Public lobbies use their message link.')
                import secrets
                token = secrets.token_urlsafe(8)
                db.rotate_lfg_share_token(event['id'], token)
                return await interaction.response.send_message(f'Old code disabled. New private access: `{token}`', ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            if action == 'Leave Lobby':
                rules.remove(event['id'], interaction.guild.id, interaction.user.id, interaction.user.id)
                try:
                    await revoke_access(interaction.guild, event, interaction.user.id)
                    result = 'You left the lobby.'
                except discord.HTTPException:
                    result = 'You left. Discord access cleanup failed; please ask staff to remove the channel override.'
                await changed(interaction.guild, event)
            elif action == 'Open / Join Voice':
                from cogs.lfg import create_event_voice
                voice = interaction.guild.get_channel(event['voice_channel_id']) if event.get('voice_channel_id') else None
                if not voice and interaction.user.id == event['host_id']:
                    voice = await create_event_voice(interaction.guild, event)
                result = f'🎧 {voice.jump_url}' if voice else 'Voice is not open yet. The host can open it or wait for the scheduled reminder.'
            else:
                raise ValueError('Unknown action.')
            await interaction.edit_original_response(content=result, view=None)
        except ValueError as exc:
            await error(interaction, exc)


class LobbyPanel(BackView):
    def __init__(self, event, user_id):
        super().__init__(event['id'], user_id)
        self.add_item(ActionSelect(event, user_id))


async def open_panel(interaction, event_id, *, update=False):
    try:
        event = authorized_event(interaction, event_id)
        kwargs = dict(content=render_event(interaction.guild, event), view=LobbyPanel(event, interaction.user.id), allowed_mentions=discord.AllowedMentions.none())
        if update:
            await interaction.response.edit_message(**kwargs)
        else:
            await interaction.response.send_message(**kwargs, ephemeral=True)
    except ValueError as exc:
        await error(interaction, exc)
