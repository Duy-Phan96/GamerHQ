from services.response_service import SafeView, command_error
"""Ephemeral room controls; callbacks reauthorize against current ownership."""
import discord
from discord import app_commands
from discord.ext import commands
from database import db
from services import temp_voice_service as service


async def perform(interaction, channel_id, action, value=None):
    await interaction.response.defer(ephemeral=True)
    try:
        result = await service.act(interaction.guild, interaction.user, channel_id, action, value)
    except ValueError as exc:
        result = str(exc)
    except discord.HTTPException:
        result = '❌ Discord could not apply this change. Check bot permissions and retry.'
    await interaction.followup.send(result, ephemeral=True)


class VoiceValueModal(discord.ui.Modal):
    def __init__(self, channel_id, action):
        super().__init__(title='Rename Voice' if action == 'rename' else 'Voice User Limit')
        self.channel_id, self.action = channel_id, action
        self.value = discord.ui.TextInput(label='Name' if action == 'rename' else 'Limit: 0–99 (0 = unlimited)', max_length=100 if action == 'rename' else 2)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        await perform(interaction, self.channel_id, self.action, str(self.value))


class VoicePanel(SafeView):
    def __init__(self, channel_id):
        super().__init__(timeout=300)
        self.channel_id = channel_id

    async def interaction_check(self, interaction):
        try:
            service.resolve(interaction.guild, interaction.user, self.channel_id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Rename', row=0)
    async def rename(self, interaction, button):
        await interaction.response.send_modal(VoiceValueModal(self.channel_id, 'rename'))

    @discord.ui.button(label='User Limit', row=0)
    async def limit(self, interaction, button):
        await interaction.response.send_modal(VoiceValueModal(self.channel_id, 'limit'))

    @discord.ui.button(label='Lock', row=0)
    async def lock(self, interaction, button):
        await perform(interaction, self.channel_id, 'lock')

    @discord.ui.button(label='Unlock', row=0)
    async def unlock(self, interaction, button):
        await perform(interaction, self.channel_id, 'unlock')

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder='Invite / allow a player', row=1)
    async def invite(self, interaction, select):
        await perform(interaction, self.channel_id, 'invite', select.values[0].id)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder='Remove a player from this room', row=2)
    async def remove(self, interaction, select):
        await perform(interaction, self.channel_id, 'remove', select.values[0].id)

    @discord.ui.button(label='Close Voice', style=discord.ButtonStyle.danger, row=3)
    async def close(self, interaction, button):
        view = VoiceClose(self.channel_id)
        await interaction.response.send_message('Delete this temporary room and disconnect its remaining occupants?', view=view, ephemeral=True)


class VoiceClose(SafeView):
    def __init__(self, channel_id):
        super().__init__(timeout=60)
        self.channel_id = channel_id
        self.used = False

    @discord.ui.button(label='Confirm Close', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.used:
            return await interaction.response.send_message('This confirmation was already used.', ephemeral=True)
        self.used = True
        await perform(interaction, self.channel_id, 'close')
        self.stop()

    @discord.ui.button(label='Cancel')
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='Cancelled.', view=None)
        self.stop()


class VoiceControls(commands.Cog):
    cog_app_command_error = command_error
    voice = app_commands.Group(name='voice', description='Manage your GamerHQ temporary voice')

    @voice.command(name='manage', description='Manage your own Create Voice room; staff may select a room.')
    @app_commands.guild_only()
    async def manage(self, interaction: discord.Interaction, channel: discord.VoiceChannel | None = None):
        if channel is None:
            current = interaction.user.voice.channel if interaction.user.voice else None
            if current and db.get_temp_voice(current.id):
                channel = current
            else:
                owned = [interaction.guild.get_channel(cid) for cid in db.get_temp_voice_ids() if db.get_temp_voice(cid)['host_id'] == interaction.user.id and db.get_temp_voice(cid)['game_id'] >= 0]
                owned = [c for c in owned if c]
                channel = owned[0] if len(owned) == 1 else None
        try:
            if channel is None:
                raise ValueError('Join your temporary room or select your room with the channel option.')
            service.resolve(interaction.guild, interaction.user, channel.id)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.send_message(f'🔊 Temporary Voice Controls — {channel.mention}', view=VoicePanel(channel.id), ephemeral=True)


async def setup(bot):
    await bot.add_cog(VoiceControls())
