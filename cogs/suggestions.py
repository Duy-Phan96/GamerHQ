"""Private suggestion delivery with restart-safe, message-bound staff controls."""
import asyncio
import logging
import sqlite3
import time

import discord
from discord.ext import commands

from database import db
from services.onboarding_service import alias, is_staff, unique
from services.server_service import ServerMessageError, upsert_fixed_message

ENTRY_TEXT = '💡 Suggestions\n\nHave an idea for GamerHQ, a bot, an event or an improvement?'
SUCCESS = '✅ Thanks! Your suggestion has been sent to the team.'
TRANSITIONS = {'NEW': {'REVIEWING','ACCEPTED','DECLINED'}, 'REVIEWING': {'ACCEPTED','DECLINED'}, 'ACCEPTED': {'REVIEWING','DECLINED','IMPLEMENTED'}, 'DECLINED': {'REVIEWING'}, 'IMPLEMENTED': set()}
STAFF_ALIASES = {'staff', 'staff-area', 'moderator', 'moderators', 'mods', 'mod', 'team'}
_locks = {}
log = logging.getLogger(__name__)


def staff_member(member):
    return bool(member.guild and (member.id == member.guild.owner_id or any(is_staff(r) for r in member.roles)))


def private_overwrites(guild, channel=None):
    result = {}
    # @everyone's deny covers ordinary roles without creating an overwrite for
    # every game role (Discord limits the number of channel overwrites).
    for target in set(channel.overwrites if channel else {}) | {r for r in guild.roles if is_staff(r)} | {guild.default_role}:
        overwrite = channel.overwrites_for(target) if channel else discord.PermissionOverwrite()
        allowed = target in guild.roles and is_staff(target)
        overwrite.view_channel = bool(allowed)
        if allowed:
            overwrite.send_messages = overwrite.read_message_history = True
        result[target] = overwrite
    if guild.me:
        result[guild.me] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True)
    return result


def inbox(guild):
    channel = unique(guild.text_channels, 'staff-suggestions')
    if not channel or not channel.category or alias(channel.category.name) not in STAFF_ALIASES:
        raise ServerMessageError('Private staff-suggestions is unavailable. Ask the owner to run /server setup.')
    if channel.overwrites_for(guild.default_role).view_channel is not False:
        raise ServerMessageError('Staff suggestions privacy needs repair; nothing was sent.')
    for target, overwrite in channel.overwrites.items():
        if overwrite.view_channel is True and target != guild.me and not (target in guild.roles and is_staff(target)):
            raise ServerMessageError('Staff suggestions has non-staff access; nothing was sent.')
    return channel


def get_suggestion(message_id, guild_id):
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM suggestions WHERE staff_message_id=? AND guild_id=?', (message_id, guild_id)).fetchone()
        return dict(row) if row else None


def card(item):
    embed = discord.Embed(title='💡 SUGGESTION', colour=discord.Colour.blurple())
    embed.add_field(name='From', value=f'<@{item["author_discord_id"]}>', inline=False)
    embed.add_field(name='Title', value=item['title'], inline=False)
    embed.add_field(name='Suggestion', value=item['content'], inline=False)
    if item['reason']:
        embed.add_field(name='Why useful', value=item['reason'], inline=False)
    embed.add_field(name='Status', value=item['status'], inline=False)
    embed.set_footer(text=f'GamerHQ suggestion #{item["id"]}')
    return embed


async def submit(interaction, title, content, reason=''):
    title, content, reason = title.strip(), content.strip(), reason.strip()
    if not interaction.guild or not title or not content or len(title) > 100 or len(content) > 1000 or len(reason) > 1000:
        await interaction.response.send_message('Please provide a title (1–100 characters) and idea (1–1000 characters); the optional reason allows 1000 characters.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        channel = inbox(interaction.guild)
        now = int(time.time())
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            duplicate = conn.execute('SELECT staff_message_id FROM suggestions WHERE guild_id=? AND author_discord_id=? AND title=? AND content=? AND reason=? AND created_at>=? ORDER BY id DESC LIMIT 1', (interaction.guild.id, interaction.user.id, title, content, reason, now-30)).fetchone()
            if duplicate:
                sid = None
            else:
                cursor = conn.execute('INSERT INTO suggestions (guild_id,author_discord_id,title,content,reason,created_at,updated_at,staff_channel_id) VALUES (?,?,?,?,?,?,?,?)', (interaction.guild.id, interaction.user.id, title, content, reason, now, now, channel.id))
                sid = cursor.lastrowid
        if sid is None:
            await interaction.followup.send(SUCCESS if duplicate['staff_message_id'] else 'ℹ️ This suggestion is already being processed. If delivery is not confirmed, contact an admin before retrying.', ephemeral=True)
            return
        item = dict(id=sid, author_discord_id=interaction.user.id, title=title, content=content, reason=reason, status='NEW')
        message = await channel.send(embed=card(item), view=StaffSuggestionView(), allowed_mentions=discord.AllowedMentions.none())
        with db.connect() as conn:
            conn.execute('UPDATE suggestions SET staff_message_id=? WHERE id=?', (message.id, sid))
    except (ServerMessageError, discord.HTTPException, sqlite3.Error):
        log.exception('Suggestion delivery failed in guild %s', interaction.guild.id)
        await interaction.followup.send('❌ Delivery could not be confirmed. Please contact an admin before retrying.', ephemeral=True)
        return
    await interaction.followup.send(SUCCESS, ephemeral=True)


class SuggestionModal(discord.ui.Modal, title='Submit Suggestion'):
    heading = discord.ui.TextInput(label='Title', required=True, max_length=100)
    idea = discord.ui.TextInput(label='Your Idea', required=True, style=discord.TextStyle.paragraph, max_length=1000)
    reason = discord.ui.TextInput(label='Why would this be useful?', required=False, style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction):
        await submit(interaction, str(self.heading), str(self.idea), str(self.reason))


class SuggestionEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Submit Suggestion', style=discord.ButtonStyle.primary, custom_id='gamerhq:suggestions:submit')
    async def open_modal(self, interaction, button):
        await interaction.response.send_modal(SuggestionModal())


class StaffSuggestionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, status in [('Review', 'REVIEWING'), ('Accept', 'ACCEPTED'), ('Decline', 'DECLINED'), ('Implemented', 'IMPLEMENTED')]:
            button = discord.ui.Button(label=label, custom_id=f'gamerhq:suggestions:{status}', style=discord.ButtonStyle.secondary)
            async def callback(interaction, status=status):
                await self.change_status(interaction, status)
            button.callback = callback
            self.add_item(button)

    async def change_status(self, interaction, status):
        if not interaction.guild or not staff_member(interaction.user):
            await interaction.response.send_message('❌ Staff only.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with _locks.setdefault(interaction.message.id, asyncio.Lock()):
            try:
                channel = inbox(interaction.guild)
                item = get_suggestion(interaction.message.id, interaction.guild.id)
                if not item or item['staff_channel_id'] != channel.id or interaction.channel_id != channel.id:
                    raise ServerMessageError('This is not a managed staff suggestion.')
                if status not in TRANSITIONS:
                    raise ServerMessageError('Unknown suggestion status.')
                if status != item['status'] and status not in TRANSITIONS.get(item['status'], set()):
                    raise ServerMessageError('That status change is not available. Declined suggestions must return to review; implemented suggestions are final.')
                # Persist first: a restart can reconstruct/retry the display from SQLite.
                with db.connect() as conn:
                    conn.execute('UPDATE suggestions SET status=?, updated_at=? WHERE id=?', (status, int(time.time()), item['id']))
                log.warning('suggestion timestamp=%s actor=%s target=%s status=%s', int(time.time()), interaction.user.id, item['id'], status)
                item['status'] = status
                await interaction.message.edit(embed=card(item), view=StaffSuggestionView(), allowed_mentions=discord.AllowedMentions.none())
            except (ServerMessageError, discord.HTTPException, sqlite3.Error) as exc:
                log.exception('Suggestion status/card update failed')
                message = str(exc) if isinstance(exc, ServerMessageError) else 'Could not refresh the staff card. Use Refresh/restart recovery after checking bot permissions.'
                await interaction.followup.send(f'❌ {message}', ephemeral=True)
                return
        await interaction.followup.send(f'Status: {status}', ephemeral=True)


async def refresh_entry(channel):
    return await upsert_fixed_message(channel, setting_key=f'suggestions_entry:{channel.guild.id}', content=ENTRY_TEXT,
        pin=True, view=SuggestionEntryView(), recover_match=lambda m: (m.content or '') == ENTRY_TEXT)


class Suggestions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(SuggestionEntryView())
        self.bot.add_view(StaffSuggestionView())

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                await reconcile_cards(guild)
            except (ServerMessageError, discord.HTTPException, sqlite3.Error):
                log.exception('Suggestion card recovery skipped in guild %s', guild.id)


async def reconcile_cards(guild):
    """Recover a send/DB crash window and refresh persisted states, without new posts."""
    with db.connect() as conn:
        items = [dict(row) for row in conn.execute('SELECT * FROM suggestions WHERE guild_id=?', (guild.id,))]
    if not items:
        return
    channel = inbox(guild)
    unmapped = {item['id']: item for item in items if not item['staff_message_id'] and item['staff_channel_id'] == channel.id}
    if unmapped:
        async for message in channel.history(limit=500):
            if not guild.me or message.author.id != guild.me.id:
                continue
            for embed in message.embeds:
                marker = embed.footer.text or ''
                prefix = 'GamerHQ suggestion #'
                suffix = marker.removeprefix(prefix)
                if marker.startswith(prefix) and suffix.isdigit() and int(suffix) in unmapped:
                    item = unmapped.pop(int(suffix))
                    item['staff_message_id'] = message.id
                    with db.connect() as conn:
                        conn.execute('UPDATE suggestions SET staff_message_id=? WHERE id=? AND staff_message_id IS NULL', (message.id, item['id']))
            if not unmapped:
                break
    for item in items:
        if item['staff_channel_id'] != channel.id or not item['staff_message_id']:
            continue
        async with _locks.setdefault(item['staff_message_id'], asyncio.Lock()):
            item = get_suggestion(item['staff_message_id'], guild.id)
            try:
                message = await channel.fetch_message(item['staff_message_id'])
            except discord.NotFound:
                continue
            if guild.me and message.author.id == guild.me.id:
                await message.edit(embed=card(item), view=StaffSuggestionView(), allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Suggestions(bot))
