import re
import time
import discord
from discord import app_commands
from discord.ext import commands
from database import db
from services.server_service import pin_managed_message, cleanup_pin_system_messages

CATEGORY = '🎥 STREAMERS'
GUIDE = '📖・streamer-guide'
CHOOSE = '🎬・choose-streamers'
UPDATES = '🔴・stream-updates'
STREAMER_ROLE = '🎥 Streamer'

GUIDE_TEXT = '''# 🎥 GamerHQ Streamers

**Discover streamers. Promote your stream. Grow your community.**

Stream on Twitch? Set up your GamerHQ Streamer profile so players can discover you.

## 🚀 Set Up Your Streamer Profile
Use **Set Up Streamer Profile** below or `/streamer setup`.

You'll add:
🎥 **Platform** — Twitch for the first version
🔗 **Twitch Channel** — Your Twitch channel
🎮 **Main Game** — Your primary game on GamerHQ
📝 **Description** — A short description shown in `choose-streamers`

There is **no application or staff approval**. Streamer profiles are open to GamerHQ members.

## 🔗 Twitch Connection
A Twitch channel is required. Full Twitch OAuth verification is the next integration step; until then GamerHQ stores the channel you provide and marks it as not yet verified.

## 🔴 Promotion & Discovery
Your profile appears in `choose-streamers`. Members can follow or unfollow Streamers there. Automatic live promotion in `stream-updates` will be enabled with the Twitch integration.

## ⭐ Optional Streamer Area
Your personal Streamer area is optional. Use `/streamer area` to create it.

You can create up to **3 permanent community channels** (text or voice) with `/streamer channels`.

GamerHQ also creates a permanent `➕・create-voice` generator for your area. It is a system channel and **does not count toward the 3-channel limit**. Join it to create a temporary Voice room. Empty temporary Voice rooms are deleted automatically.

## 🛠️ Streamer Commands
`/streamer setup` — Create or update your Streamer profile.
`/streamer profile` — View your current Streamer profile.
`/streamer audience` — See who follows you on GamerHQ.
`/streamer area` — Create or repair your optional Streamer area.
`/streamer channels` — Create, rename or delete your 3 permanent community channels.
`/streamer voice` — Create a temporary Voice room in your Streamer area.

Use the selector in `choose-streamers` to follow or unfollow a Streamer.
'''



def find_channel(guild, name):
    return discord.utils.get(guild.text_channels, name=name)


async def ensure_streamer_role(guild):
    role = discord.utils.get(guild.roles, name=STREAMER_ROLE)
    return role or await guild.create_role(name=STREAMER_ROLE, reason='GamerHQ streamer system')


def normalize_twitch_channel(value: str) -> str | None:
    value = value.strip()
    value = re.sub(r'^https?://(www\.)?twitch\.tv/', '', value, flags=re.I)
    value = value.split('?', 1)[0].strip('/ ')
    if not re.fullmatch(r'[A-Za-z0-9_]{3,25}', value):
        return None
    return value


class SetupButtonView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label='Set Up Streamer Profile',
        emoji='🎥',
        style=discord.ButtonStyle.primary,
        custom_id='gamerhq:streamer:setup',
    )
    async def setup_profile(self, interaction, button):
        await self.cog.start_setup(interaction)


class GameSelect(discord.ui.Select):
    def __init__(self, cog, user_id, games):
        self.cog = cog
        self.user_id = user_id
        opts = [
            discord.SelectOption(
                label=g['name'][:100],
                value=str(g['id']),
                emoji=g.get('emoji') or '🎮',
            )
            for g in games[:25]
        ]
        super().__init__(
            placeholder='Choose your main game…',
            options=opts,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                'This setup belongs to another user.', ephemeral=True
            )
        profile = db.get_streamer_profile(interaction.guild.id, interaction.user.id)
        await interaction.response.send_modal(
            StreamerProfileModal(self.cog, int(self.values[0]), profile)
        )


class GameView(discord.ui.View):
    def __init__(self, cog, user_id, games):
        super().__init__(timeout=180)
        self.add_item(GameSelect(cog, user_id, games))


class StreamerProfileModal(discord.ui.Modal, title='🎥 Streamer Profile'):
    twitch_channel = discord.ui.TextInput(
        label='Twitch channel',
        placeholder='e.g. EinfachKamex or twitch.tv/EinfachKamex',
        max_length=100,
    )
    description = discord.ui.TextInput(
        label='Stream description',
        placeholder='What can GamerHQ members expect from your stream?',
        style=discord.TextStyle.paragraph,
        min_length=20,
        max_length=250,
    )

    def __init__(self, cog, game_id, profile=None):
        super().__init__()
        self.cog = cog
        self.game_id = game_id
        if profile:
            self.twitch_channel.default = profile.get('channel') or ''
            self.description.default = profile.get('description') or ''

    async def on_submit(self, interaction):
        await self.cog.save_profile(
            interaction,
            self.game_id,
            str(self.twitch_channel),
            str(self.description),
        )


class StreamerFollowSelect(discord.ui.Select):
    def __init__(self, cog, profiles, guild):
        self.cog = cog
        options = []
        for p in profiles[:25]:
            member = guild.get_member(p['user_id'])
            game = db.get_game_by_id(p['main_game_id'])
            name = member.display_name if member else f"Streamer {p['user_id']}"
            game_name = game['name'] if game else 'Unknown Game'
            options.append(discord.SelectOption(
                label=name[:100],
                value=str(p['user_id']),
                description=f"{game_name} • Follow / unfollow"[:100],
                emoji='🎥',
            ))
        super().__init__(
            placeholder='Choose a Streamer to follow / unfollow…',
            options=options or [discord.SelectOption(label='No Streamers yet', value='none')],
            min_values=1,
            max_values=1,
            custom_id='gamerhq:streamer:follow-select',
            disabled=not bool(options),
        )

    async def callback(self, interaction):
        if self.values[0] == 'none':
            return await interaction.response.send_message('No Streamers are available yet.', ephemeral=True)
        streamer_id = int(self.values[0])
        p = db.get_streamer_profile(interaction.guild.id, streamer_id)
        if not p:
            return await interaction.response.send_message('That Streamer profile no longer exists.', ephemeral=True)
        role = await self.cog.ensure_follower_role(interaction.guild, p)
        if not role:
            return await interaction.response.send_message('I could not create the Streamer follow role.', ephemeral=True)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason='GamerHQ Streamer unfollow')
            action = 'unfollowed'
            emoji = '🔕'
        else:
            await interaction.user.add_roles(role, reason='GamerHQ Streamer follow')
            action = 'followed'
            emoji = '🔔'
        member = interaction.guild.get_member(streamer_id)
        name = member.display_name if member else 'this Streamer'
        await interaction.response.send_message(
            f'{emoji} You **{action} {name}**.', ephemeral=True
        )
        await self.cog.refresh_choose(interaction.guild)


class StreamerFollowView(discord.ui.View):
    def __init__(self, cog, profiles, guild):
        super().__init__(timeout=None)
        self.add_item(StreamerFollowSelect(cog, profiles, guild))



class StreamerChannelNameModal(discord.ui.Modal, title='➕ Add Streamer Channel'):
    channel_name = discord.ui.TextInput(
        label='Channel name',
        placeholder='e.g. chat, clips, community-lounge',
        min_length=1,
        max_length=80,
    )

    def __init__(self, cog, user_id, channel_kind):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.channel_kind = channel_kind

    async def on_submit(self, interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await self.cog.create_streamer_channel(
            interaction,
            str(self.channel_name),
            self.channel_kind,
        )


class StreamerRenameChannelModal(discord.ui.Modal, title='✏️ Rename Streamer Channel'):
    channel_name = discord.ui.TextInput(
        label='New channel name',
        min_length=1,
        max_length=80,
    )

    def __init__(self, cog, user_id, channel_id, current_name):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel_id
        self.channel_name.default = current_name

    async def on_submit(self, interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await self.cog.rename_streamer_channel(
            interaction,
            self.channel_id,
            str(self.channel_name),
        )


class StreamerAddChannelTypeSelect(discord.ui.Select):
    def __init__(self, cog, user_id):
        self.cog = cog
        self.user_id = user_id
        super().__init__(
            placeholder='Choose a channel type…',
            options=[
                discord.SelectOption(
                    label='Text Channel', value='text', emoji='💬',
                    description='A permanent text channel in your Streamer area.'
                ),
                discord.SelectOption(
                    label='Voice Channel', value='voice', emoji='🔊',
                    description='A permanent voice channel in your Streamer area.'
                ),
            ],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await interaction.response.send_modal(
            StreamerChannelNameModal(self.cog, self.user_id, self.values[0])
        )


class StreamerAddChannelTypeView(discord.ui.View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.add_item(StreamerAddChannelTypeSelect(cog, user_id))

    @discord.ui.button(label='Back', emoji='↩️', style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await self.cog.render_channel_manager(interaction, notice=None)


class StreamerManageChannelSelect(discord.ui.Select):
    def __init__(self, cog, user_id, channels):
        self.cog = cog
        self.user_id = user_id
        opts = []
        for ch in channels[:25]:
            emoji = '🔊' if isinstance(ch, discord.VoiceChannel) else '💬'
            kind = 'Voice Channel' if isinstance(ch, discord.VoiceChannel) else 'Text Channel'
            opts.append(discord.SelectOption(
                label=ch.name[:100],
                value=str(ch.id),
                emoji=emoji,
                description=f'Manage this {kind.lower()}'[:100],
            ))
        super().__init__(
            placeholder='Choose a channel to manage…',
            options=opts or [discord.SelectOption(label='No channels to manage', value='none')],
            min_values=1,
            max_values=1,
            disabled=not bool(opts),
        )

    async def callback(self, interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        if self.values[0] == 'none':
            return await interaction.response.send_message('You do not have any managed Streamer channels yet.', ephemeral=True)
        channel_id = int(self.values[0])
        record = db.get_streamer_channel(interaction.guild.id, self.user_id, channel_id)
        channel = interaction.guild.get_channel(channel_id)
        if not record or not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            if record:
                db.remove_streamer_channel(channel_id)
            return await self.cog.render_channel_manager(
                interaction,
                notice='⚠️ That channel no longer exists. The Streamer manager has been refreshed.',
            )
        kind = 'Voice Channel' if isinstance(channel, discord.VoiceChannel) else 'Text Channel'
        emoji = '🔊' if isinstance(channel, discord.VoiceChannel) else '💬'
        await interaction.response.edit_message(
            content=(
                '# 🛠️ Manage Streamer Channel\n\n'
                f'{emoji} **{channel.name}**\n'
                f'**Type:** {kind}\n\n'
                'Choose what you want to do with this channel.'
            ),
            view=StreamerSelectedChannelView(self.cog, self.user_id, channel),
        )


class StreamerManageChannelSelectView(discord.ui.View):
    def __init__(self, cog, user_id, channels):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.add_item(StreamerManageChannelSelect(cog, user_id, channels))

    @discord.ui.button(label='Back', emoji='↩️', style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await self.cog.render_channel_manager(interaction)


class StreamerSelectedChannelView(discord.ui.View):
    def __init__(self, cog, user_id, channel):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel.id
        self.current_name = channel.name

    @discord.ui.button(label='Rename', emoji='✏️', style=discord.ButtonStyle.primary)
    async def rename(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            return await self.cog.render_channel_manager(interaction, notice='⚠️ That channel no longer exists.')
        await interaction.response.send_modal(
            StreamerRenameChannelModal(
                self.cog, self.user_id, self.channel_id, channel.name
            )
        )

    @discord.ui.button(label='Delete', emoji='🗑️', style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            db.remove_streamer_channel(self.channel_id)
            return await self.cog.render_channel_manager(interaction, notice='⚠️ That channel no longer exists.')
        await interaction.response.edit_message(
            content=(
                '# 🗑️ Delete Streamer Channel?\n\n'
                f'You are about to permanently delete **{channel.name}**.\n\n'
                '**This cannot be undone.**'
            ),
            view=StreamerDeleteConfirmView(self.cog, self.user_id, self.channel_id),
        )

    @discord.ui.button(label='Back', emoji='↩️', style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await self.cog.show_manage_selector(interaction)


class StreamerDeleteConfirmView(discord.ui.View):
    def __init__(self, cog, user_id, channel_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel_id

    @discord.ui.button(label='Delete Channel', emoji='🗑️', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await self.cog.delete_streamer_channel(interaction, self.channel_id)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await self.cog.render_channel_manager(interaction)


class StreamerChannelManagerView(discord.ui.View):
    def __init__(self, cog, user_id, channels):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.channels = channels
        self.add_channel.disabled = len(channels) >= 3
        self.manage_channel.disabled = not bool(channels)

    @discord.ui.button(label='Add Channel', emoji='➕', style=discord.ButtonStyle.success)
    async def add_channel(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        if len(self.channels) >= 3:
            return await interaction.response.send_message('❌ You already use all **3/3** permanent Streamer channels.', ephemeral=True)
        await interaction.response.edit_message(
            content=(
                '# ➕ Add Streamer Channel\n\n'
                'Choose the type of permanent channel you want to create.\n\n'
                'Your `➕・create-voice` generator is separate and does **not** count toward the 3-channel limit.'
            ),
            view=StreamerAddChannelTypeView(self.cog, self.user_id),
        )

    @discord.ui.button(label='Manage Channel', emoji='🛠️', style=discord.ButtonStyle.primary)
    async def manage_channel(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('This menu belongs to another user.', ephemeral=True)
        await self.cog.show_manage_selector(interaction)

class Streamer(commands.Cog):
    streamer = app_commands.Group(name='streamer', description='GamerHQ streamer tools')

    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _safe_channel_name(value: str) -> str:
        value = value.strip().lower().replace(' ', '-')
        value = re.sub(r'[^a-z0-9\-_äöüß]', '', value)
        value = re.sub(r'-{2,}', '-', value).strip('-_')
        return value[:80] or 'streamer-chat'

    async def ensure_streamer_area(self, guild, member, profile):
        if not profile:
            return None, None

        role = await self.ensure_follower_role(guild, profile)
        category_id = profile.get('category_id')
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            wanted = f'⭐ {member.display_name.upper()}'[:100]
            category = discord.utils.get(guild.categories, name=wanted)

        bot_member = guild.me
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        }
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True, manage_channels=True, manage_roles=True, connect=True, move_members=True
            )

        if category is None:
            category = await guild.create_category(
                f'⭐ {member.display_name.upper()}'[:100],
                overwrites=overwrites,
                reason='GamerHQ Streamer area',
            )
        else:
            try:
                await category.edit(overwrites=overwrites, reason='Repair GamerHQ Streamer area permissions')
            except discord.HTTPException:
                pass

        create_voice_id = profile.get('create_voice_channel_id')
        generator = guild.get_channel(create_voice_id) if create_voice_id else None
        if not isinstance(generator, discord.VoiceChannel):
            generator = discord.utils.get(category.voice_channels, name='➕・create-voice')
        if generator is None:
            generator = await guild.create_voice_channel(
                '➕・create-voice', category=category, reason='GamerHQ Streamer Create Voice generator'
            )

        db.set_streamer_area(guild.id, member.id, category.id, generator.id)
        return category, generator

    async def create_streamer_temp_voice(self, guild, member, profile):
        category, generator = await self.ensure_streamer_area(guild, member, profile)
        if not category:
            return None
        role = await self.ensure_follower_role(guild, profile)
        bot_member = guild.me
        overwrites = dict(category.overwrites)
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, manage_channels=True, move_members=True
        )
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True, move_members=True
            )
        temp = await guild.create_voice_channel(
            name=f'🔊 {member.display_name}\'s Room'[:100],
            category=category,
            overwrites=overwrites,
            reason='GamerHQ Streamer temporary voice',
        )
        db.add_temp_voice(temp.id, member.id, -member.id)
        return temp

    def get_managed_streamer_channels(self, guild, user_id):
        records = db.get_streamer_channels(guild.id, user_id)
        channels = []
        for record in records:
            channel = guild.get_channel(record['channel_id'])
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                channels.append(channel)
            else:
                # Keep the database clean when a Streamer deletes a managed
                # channel manually in Discord or it disappears for another reason.
                db.remove_streamer_channel(record['channel_id'])
        return channels

    def channel_manager_content(self, channels, notice=None):
        lines = [
            '# 🛠️ Manage Streamer Channels',
            '',
            f'**Used:** {len(channels)}/3',
            '',
        ]
        if notice:
            lines.extend([notice, ''])
        if channels:
            for ch in channels:
                emoji = '🔊' if isinstance(ch, discord.VoiceChannel) else '💬'
                kind = 'Voice' if isinstance(ch, discord.VoiceChannel) else 'Text'
                lines.append(f'{emoji} {ch.mention} — **{kind}**')
        else:
            lines.append('*No permanent community channels yet.*')
        lines.extend([
            '',
            '**Add Channel** creates a permanent text or voice channel.',
            '**Manage Channel** lets you rename or delete an existing channel.',
        ])
        return '\n'.join(lines)

    async def render_channel_manager(self, interaction, notice=None):
        channels = self.get_managed_streamer_channels(interaction.guild, interaction.user.id)
        content = self.channel_manager_content(channels, notice)
        view = StreamerChannelManagerView(self, interaction.user.id, channels)
        if interaction.response.is_done():
            return await interaction.followup.send(content, view=view, ephemeral=True)
        try:
            return await interaction.response.edit_message(content=content, view=view)
        except (discord.HTTPException, AttributeError):
            return await interaction.response.send_message(content, view=view, ephemeral=True)

    async def show_manage_selector(self, interaction):
        channels = self.get_managed_streamer_channels(interaction.guild, interaction.user.id)
        if not channels:
            return await self.render_channel_manager(interaction, notice='ℹ️ Create a channel first, then you can manage it here.')
        content = (
            '# 🛠️ Manage a Streamer Channel\n\n'
            'Choose one of your permanent channels below. You can then **rename** or **delete** it.'
        )
        view = StreamerManageChannelSelectView(self, interaction.user.id, channels)
        if interaction.response.is_done():
            return await interaction.followup.send(content, view=view, ephemeral=True)
        return await interaction.response.edit_message(content=content, view=view)

    async def create_streamer_channel(self, interaction, requested_name, channel_kind):
        profile = db.get_streamer_profile(interaction.guild.id, interaction.user.id)
        if not profile:
            return await interaction.response.send_message('Create your Streamer profile first with `/streamer setup`.', ephemeral=True)
        category, _ = await self.ensure_streamer_area(interaction.guild, interaction.user, profile)
        existing = self.get_managed_streamer_channels(interaction.guild, interaction.user.id)
        if len(existing) >= 3:
            return await self.render_channel_manager(interaction, notice='❌ Your Streamer area already has the maximum of **3 permanent community channels**.')
        name = self._safe_channel_name(requested_name)
        if discord.utils.get(category.channels, name=name):
            return await self.render_channel_manager(interaction, notice=f'❌ A channel named `{name}` already exists in your Streamer area.')

        if channel_kind == 'voice':
            channel = await interaction.guild.create_voice_channel(
                name,
                category=category,
                reason='GamerHQ Streamer managed voice channel',
            )
        else:
            channel_kind = 'text'
            channel = await interaction.guild.create_text_channel(
                name,
                category=category,
                reason='GamerHQ Streamer managed text channel',
            )

        db.add_streamer_channel(interaction.guild.id, interaction.user.id, channel.id, channel_kind)
        await self.render_channel_manager(
            interaction,
            notice=f'✅ Created {channel.mention}.',
        )

    async def rename_streamer_channel(self, interaction, channel_id, requested_name):
        record = db.get_streamer_channel(interaction.guild.id, interaction.user.id, channel_id)
        channel = interaction.guild.get_channel(channel_id)
        if not record or not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            if record:
                db.remove_streamer_channel(channel_id)
            return await self.render_channel_manager(interaction, notice='⚠️ That channel no longer exists.')

        name = self._safe_channel_name(requested_name)
        duplicate = discord.utils.get(channel.category.channels, name=name) if channel.category else None
        if duplicate and duplicate.id != channel.id:
            return await self.render_channel_manager(interaction, notice=f'❌ A channel named `{name}` already exists in your Streamer area.')
        try:
            await channel.edit(name=name, reason='Streamer renamed managed channel')
        except discord.HTTPException:
            return await self.render_channel_manager(interaction, notice='❌ I could not rename that channel. Please check my permissions.')
        await self.render_channel_manager(interaction, notice=f'✅ Renamed the channel to **{name}**.')

    async def delete_streamer_channel(self, interaction, channel_id):
        record = db.get_streamer_channel(interaction.guild.id, interaction.user.id, channel_id)
        if not record:
            return await self.render_channel_manager(interaction, notice='⚠️ That channel is no longer managed by your Streamer profile.')
        channel = interaction.guild.get_channel(channel_id)
        db.remove_streamer_channel(channel_id)
        if channel:
            try:
                await channel.delete(reason='Streamer removed managed channel')
            except discord.HTTPException:
                # Restore management if Discord refused the deletion.
                db.add_streamer_channel(
                    interaction.guild.id,
                    interaction.user.id,
                    channel_id,
                    record.get('channel_kind') or 'text',
                )
                return await self.render_channel_manager(interaction, notice='❌ I could not delete that channel. Please check my permissions.')
        await self.render_channel_manager(interaction, notice='✅ Streamer channel deleted.')

    async def ensure_follower_role(self, guild, profile):
        role_id = profile.get('follower_role_id')
        role = guild.get_role(role_id) if role_id else None
        member = guild.get_member(profile['user_id'])
        display_name = member.display_name if member else f"Streamer {profile['user_id']}"
        wanted_name = f"🔴 Streamer • {display_name}"[:100]
        if role is None:
            role = discord.utils.get(guild.roles, name=wanted_name)
        if role is None:
            role = await guild.create_role(
                name=wanted_name,
                reason='GamerHQ Streamer follower role',
                mentionable=False,
            )
        elif role.name != wanted_name:
            try:
                await role.edit(name=wanted_name, reason='Keep GamerHQ Streamer follower role in sync')
            except discord.HTTPException:
                pass
        if profile.get('follower_role_id') != role.id:
            db.set_streamer_follower_role(guild.id, profile['user_id'], role.id)
            profile['follower_role_id'] = role.id
        return role

    async def cog_load(self):
        self.bot.add_view(SetupButtonView(self))

    async def ensure_infra(self, guild):
        cat = discord.utils.get(guild.categories, name=CATEGORY)
        if not cat:
            cat = await guild.create_category(CATEGORY, reason='GamerHQ streamer system')

        channels = {}
        for name in (GUIDE, CHOOSE, UPDATES):
            ch = find_channel(guild, name)
            if not ch:
                ch = await guild.create_text_channel(name, category=cat, reason='GamerHQ streamer system')
            elif ch.category_id != cat.id:
                await ch.edit(category=cat, reason='GamerHQ streamer system')
            channels[name] = ch

        key = 'streamer_guide_message_id'
        mid = db.get_setting(key)
        msg = None
        if mid:
            try:
                msg = await channels[GUIDE].fetch_message(int(mid))
            except (discord.NotFound, discord.HTTPException, ValueError):
                pass
        if msg:
            await msg.edit(content=GUIDE_TEXT, view=SetupButtonView(self))
        else:
            msg = await channels[GUIDE].send(GUIDE_TEXT, view=SetupButtonView(self))
            db.set_setting(key, str(msg.id))
            try:
                await pin_managed_message(msg, reason='GamerHQ streamer guide')
            except discord.HTTPException:
                pass

        await self.refresh_choose(guild)

    async def refresh_choose(self, guild):
        ch = find_channel(guild, CHOOSE)
        if not ch:
            return

        profiles = db.get_streamer_profiles(guild.id)
        for p in profiles:
            try:
                await self.ensure_follower_role(guild, p)
            except discord.HTTPException:
                pass

        # Re-read so newly created role IDs are included.
        profiles = db.get_streamer_profiles(guild.id)
        lines = [
            '# 🎬 Choose Streamers',
            '',
            '**Discover Streamers. Follow the ones you want to see on GamerHQ.**',
            '',
            'Use the selector below to **follow or unfollow** a Streamer.',
            '',
        ]
        if not profiles:
            lines.append('*No Streamer profiles yet.*')

        for p in profiles:
            member = guild.get_member(p['user_id'])
            game = db.get_game_by_id(p['main_game_id'])
            name = member.display_name if member else 'Streamer'
            game_name = game['name'] if game else 'Unknown Game'
            description = (p.get('description') or '').strip()
            twitch = p.get('channel') or ''
            verified = '✅ Connected' if p.get('twitch_connected') else '⚪ Twitch verification pending'
            lines.extend([
                f'## 🎥 {name}',
                f'🎮 **Main Game:** {game_name}',
                f'🔗 **Twitch:** https://twitch.tv/{twitch}',
                f'{verified}',
                f'📝 {description}' if description else '',
                '',
            ])

        content = '\n'.join(line for line in lines if line is not None)
        if len(content) > 1950:
            content = content[:1900] + '\n\n*More Streamers are registered. Directory pagination will be added as the list grows.*'

        key = 'streamer_choose_message_id'
        mid = db.get_setting(key)
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(int(mid))
            except (discord.NotFound, discord.HTTPException, ValueError):
                pass
        view = StreamerFollowView(self, profiles, guild)
        if msg:
            await msg.edit(content=content, view=view)
        else:
            msg = await ch.send(content, view=view)
            db.set_setting(key, str(msg.id))
            try:
                await pin_managed_message(msg, reason='GamerHQ streamer directory')
            except discord.HTTPException:
                pass

    async def start_setup(self, interaction):
        games = db.get_selectable_games()
        if not games:
            return await interaction.response.send_message(
                'No active GamerHQ games are available yet.', ephemeral=True
            )

        # Discord supports 25 select options. Prioritize games the member has
        # selected, then fill the remaining slots alphabetically.
        role_ids = {r.id for r in interaction.user.roles}
        games = sorted(
            games,
            key=lambda g: (0 if g.get('role_id') in role_ids else 1, g['name'].lower()),
        )[:25]
        existing = db.get_streamer_profile(interaction.guild.id, interaction.user.id)
        action = 'Update' if existing else 'Set Up'
        await interaction.response.send_message(
            f'**🎥 {action} Streamer Profile**\n'
            'Platform: **Twitch**\n'
            'Choose your **Main Game** first. You will enter your Twitch channel and required description next.',
            view=GameView(self, interaction.user.id, games),
            ephemeral=True,
        )

    async def save_profile(self, interaction, game_id, channel_value, description_value):
        game = db.get_game_by_id(game_id)
        if not game:
            return await interaction.response.send_message(
                'That game is no longer available.', ephemeral=True
            )

        channel = normalize_twitch_channel(channel_value)
        if not channel:
            return await interaction.response.send_message(
                '❌ Please enter a valid Twitch channel name or Twitch channel URL.',
                ephemeral=True,
            )

        description = description_value.strip()
        if len(description) < 20:
            return await interaction.response.send_message(
                '❌ Your description must be at least 20 characters long.', ephemeral=True
            )

        # Open registration: profile becomes active immediately. Twitch OAuth
        # verification is intentionally stored separately and remains false
        # until the real Twitch connection step is implemented.
        db.upsert_streamer_profile_open(
            interaction.guild.id,
            interaction.user.id,
            'Twitch',
            channel,
            game_id,
            description,
            int(time.time()),
        )
        role = await ensure_streamer_role(interaction.guild)
        if role not in interaction.user.roles:
            await interaction.user.add_roles(role, reason='GamerHQ Streamer profile created')

        await self.refresh_choose(interaction.guild)
        await interaction.response.send_message(
            '# ✅ Streamer Profile Saved\n'
            f'🎥 **Platform:** Twitch\n'
            f'🔗 **Channel:** https://twitch.tv/{channel}\n'
            f'🎮 **Main Game:** {game["name"]}\n'
            f'📝 **Description:** {description}\n\n'
            'Your profile is now listed in `choose-streamers`.\n'
            '⚪ **Twitch verification is not active yet.** The OAuth connection is the next integration step.',
            ephemeral=True,
        )

    @streamer.command(name='setup', description='Create or update your GamerHQ Streamer profile.')
    async def setup_cmd(self, interaction):
        await self.start_setup(interaction)

    @streamer.command(name='profile', description='View your GamerHQ Streamer profile.')
    async def profile(self, interaction):
        p = db.get_streamer_profile(interaction.guild.id, interaction.user.id)
        if not p:
            return await interaction.response.send_message(
                'You do not have a GamerHQ Streamer profile yet. Use `/streamer setup`.',
                ephemeral=True,
            )
        game = db.get_game_by_id(p['main_game_id'])
        verified = '✅ Connected' if p.get('twitch_connected') else '⚪ Verification pending'
        await interaction.response.send_message(
            '# 🎥 Your Streamer Profile\n'
            f'**Platform:** {p["platform"]}\n'
            f'**Twitch:** https://twitch.tv/{p["channel"]}\n'
            f'**Twitch status:** {verified}\n'
            f'**Main Game:** {game["name"] if game else "Unknown"}\n'
            f'**Description:** {p.get("description") or "—"}',
            ephemeral=True,
        )

    @streamer.command(name='audience', description='See who follows your GamerHQ Streamer profile.')
    async def audience(self, interaction):
        p = db.get_streamer_profile(interaction.guild.id, interaction.user.id)
        if not p:
            return await interaction.response.send_message(
                'You do not have a GamerHQ Streamer profile yet. Use `/streamer setup`.',
                ephemeral=True,
            )
        role = await self.ensure_follower_role(interaction.guild, p)
        members = [m for m in role.members if not m.bot and m.id != interaction.user.id]
        names = [m.display_name for m in members]
        preview = '\n'.join(f'• {name}' for name in names[:30]) or '*No followers yet.*'
        extra = ''
        if len(names) > 30:
            extra = f'\n…and **{len(names) - 30} more**.'
        await interaction.response.send_message(
            '# 👥 Your GamerHQ Audience\n'
            f'**Followers:** {len(members)}\n\n'
            f'{preview}{extra}',
            ephemeral=True,
        )

    @streamer.command(name='area', description='Create or open your optional GamerHQ Streamer area.')
    async def area(self, interaction):
        p = db.get_streamer_profile(interaction.guild.id, interaction.user.id)
        if not p:
            return await interaction.response.send_message('Create your Streamer profile first with `/streamer setup`.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        category, generator = await self.ensure_streamer_area(interaction.guild, interaction.user, p)
        await interaction.followup.send(
            '# ⭐ Streamer Area Ready\n'
            f'**Category:** {category.name}\n'
            f'**Create Voice:** {generator.mention}\n\n'
            'Your area can contain up to **3 permanent community channels** (text or voice).\n'
            'Use `/streamer channels` to manage them. The Create Voice generator does **not** count toward the limit.',
            ephemeral=True,
        )

    @streamer.command(name='channels', description='Manage your 3 permanent Streamer community channels.')
    async def channels(self, interaction):
        p = db.get_streamer_profile(interaction.guild.id, interaction.user.id)
        if not p:
            return await interaction.response.send_message('Create your Streamer profile first with `/streamer setup`.', ephemeral=True)
        await self.ensure_streamer_area(interaction.guild, interaction.user, p)
        channels = self.get_managed_streamer_channels(interaction.guild, interaction.user.id)
        await interaction.response.send_message(
            self.channel_manager_content(channels),
            view=StreamerChannelManagerView(self, interaction.user.id, channels),
            ephemeral=True,
        )

    @streamer.command(name='voice', description='Create a temporary Voice room in your Streamer area.')
    async def voice(self, interaction):
        p = db.get_streamer_profile(interaction.guild.id, interaction.user.id)
        if not p:
            return await interaction.response.send_message('Create your Streamer profile first with `/streamer setup`.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        channel = await self.create_streamer_temp_voice(interaction.guild, interaction.user, p)
        await interaction.followup.send(
            f'🔊 Created **{channel.name}**. Join it from your Streamer category. It will be deleted automatically when empty.',
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                await self.ensure_infra(guild)
            except Exception as e:
                print(f'Streamer infrastructure refresh skipped: {e}')


async def setup(bot):
    await bot.add_cog(Streamer(bot))
