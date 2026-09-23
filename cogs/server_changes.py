"""Private persistent approval buttons for managed-channel drift."""
import logging
import asyncio
import discord
from discord.ext import commands, tasks

from database import db
from services import channel_change_service as changes
from services.onboarding_service import alias, is_staff
from services.response_service import SafeView
from services.server_service import ServerMessageError

log = logging.getLogger(__name__)


def log_channel(guild):
    raw = db.get_setting(f'managed_channel:{guild.id}:bot-log')
    channel = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
    if channel is None:
        matches = [c for c in guild.text_channels if alias(c.name) == 'bot-log']
        channel = matches[0] if len(matches) == 1 else None
    if channel is None or channel.permissions_for(guild.default_role).view_channel:
        return None
    # Check each role: any non-staff explicit visibility grant could expose the log.
    if any(not is_staff(role) and role != guild.default_role and not role.managed
           and channel.permissions_for(role).view_channel for role in guild.roles):
        return None
    for target, overwrite in channel.overwrites.items():
        staff = (target in guild.roles and is_staff(target)) or (isinstance(target, discord.Member) and any(is_staff(r) for r in target.roles))
        if target != guild.me and not staff and overwrite.view_channel is True:
            return None
    return channel


def render(record):
    status = record['status']
    title = ('🚨 Security-sensitive drift auto-repaired' if status == 'auto_repaired' else
             '🚨 Security repair FAILED — immediate owner attention required' if status == 'repair_failed' else
             '⚠️ Managed channel deleted' if 'deleted' in record['change_types'] else '⚙️ Server change detected')
    lines = [title, f"Resource: {record['resource_key']} ({record['resource_id']})",
             f"Risk: {record['risk_level']} · Status: {status}"]
    for field in record['change_types']:
        if field == 'permissions':
            before = record['old_desired_state'].get(field, {})
            after = record['current_discord_state'].get(field, {})
            ids = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            lines.append('Managed permission differences:')
            for tid in ids[:6]:
                prior = discord.PermissionOverwrite.from_pair(*(discord.Permissions(v) for v in before.get(tid, [0, 0])))
                actual = discord.PermissionOverwrite.from_pair(*(discord.Permissions(v) for v in after.get(tid, [0, 0])))
                label = '@everyone' if tid == str(record['guild_id']) else f'Role/member {tid}'
                for bit in ('view_channel', 'send_messages', 'read_message_history', 'embed_links', 'attach_files',
                            'send_messages_in_threads', 'create_public_threads', 'create_private_threads', 'manage_messages'):
                    if getattr(prior, bit) != getattr(actual, bit):
                        lines.append(f'{label} {bit.replace("_", " ")}: desired {getattr(prior, bit)} → Discord {getattr(actual, bit)}')
            if len(ids) > 6:
                lines.append(f'… {len(ids) - 6} further overwrite differences; review Discord before acting.')
        else:
            lines.append(f"{field}: desired {record['old_desired_state'].get(field)} → Discord {record['current_discord_state'].get(field)}")
    if status == 'pending':
        lines.append('Only owner/admin can approve. Ignore keeps drift unresolved. Unsupported permissions cannot be adopted.')
    if status == 'auto_repaired':
        lines.append('Known-safe private/bot access restored automatically; unsafe permissions cannot be approved.')
    if record.get('restore_warning'):
        lines.append(record['restore_warning'])
    if record.get('resolution'):
        lines.append(record['resolution'])
    marker = f"Change: {record['resource_id']}/{record['revision']}"
    return '\n'.join(lines)[:1850] + '\n' + marker


class ChangeView(SafeView):
    def __init__(self, record, notify):
        super().__init__(timeout=None)
        self.record = record
        self.notify = notify
        deleted = 'deleted' in record['change_types']
        options = [('♻️ Restore Channel', 'restore'), ('🗑️ Remove from Setup', 'remove')] if deleted else [('✅ Apply to Setup', 'adopt'), ('↩️ Revert Change', 'revert')]
        options.append(('❌ Ignore', 'ignore'))
        for label, action in options:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary,
                custom_id=f"gamerhq:change:{record['guild_id']}:{record['resource_id']}:{record['revision']}:{action}")
            async def callback(interaction, action=action):
                try:
                    changes.adoption.authorize(interaction.guild, interaction.user)
                    if interaction.guild.id != self.record['guild_id']:
                        raise ServerMessageError('This approval belongs to a different server.')
                except ServerMessageError as exc:
                    await interaction.response.send_message(str(exc), ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    updated = await changes.act(interaction.guild, interaction.user, self.record['resource_id'],
                                                self.record['revision'], action)
                    await self.notify(interaction.guild, updated)
                    await interaction.followup.send(f"Managed change: {updated['status']}.", ephemeral=True)
                except (ServerMessageError, discord.HTTPException) as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
            button.callback = callback
            self.add_item(button)


class ServerChanges(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._notification_locks = {}

    async def cog_load(self):
        for record in changes.records():
            if record['status'] == 'pending' and record.get('message_id'):
                self.bot.add_view(ChangeView(record, self.notify), message_id=record['message_id'])
        self.maintenance.start()

    async def cog_unload(self):
        self.maintenance.cancel()
        for task in changes._timers.values():
            task.cancel()
        changes._timers.clear()
        changes._dirty.clear()

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if isinstance(after, discord.TextChannel):
            await changes.detect(before, after, self.notify)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if isinstance(channel, discord.TextChannel):
            await changes.detect(channel, None, self.notify, deleted=True)

    async def notify(self, guild, record):
        key = (guild.id, record['resource_id'])
        async with self._notification_locks.setdefault(key, asyncio.Lock()):
            # Delivery must never overwrite a newer approval/event record.
            record = changes.load(*key) or record
            await self._notify(guild, record)

    async def _notify(self, guild, record):
        channel = log_channel(guild)
        if not channel:
            log.warning('Managed drift retained without notification: private bot-log unavailable guild=%s', guild.id)
            return
        view = ChangeView(record, self.notify) if record['status'] == 'pending' else None
        try:
            message = None
            if record.get('message_id') and record.get('notification_channel_id') == channel.id:
                try:
                    message = await channel.fetch_message(record['message_id'])
                except discord.NotFound:
                    pass
            if message is None:
                marker = f"Change: {record['resource_id']}/{record['revision']}"
                async for candidate in channel.history(limit=30):
                    if candidate.author.id == guild.me.id and marker in (candidate.content or ''):
                        message = candidate
                        break
            if message:
                if message.author.id != guild.me.id:
                    raise ServerMessageError('Notification mapping points to another author; retained for review.')
                await message.edit(content=render(record), view=view, allowed_mentions=discord.AllowedMentions.none())
            else:
                message = await channel.send(content=render(record), view=view, allowed_mentions=discord.AllowedMentions.none())
            current = changes.load(guild.id, record['resource_id'])
            if current:
                current.update(notification_channel_id=channel.id, message_id=message.id,
                    delivered_status=record['status'] if current['revision'] == record['revision'] and current['status'] == record['status'] else None)
                changes.store(current)
        except (discord.HTTPException, ServerMessageError):
            log.exception('Managed change notification failed guild=%s', guild.id)

    @tasks.loop(seconds=60)
    async def maintenance(self):
        changes.expire()
        for record in changes.records():
            guild = self.bot.get_guild(record['guild_id'])
            if guild and record['status'] == 'repair_failed':
                channel = guild.get_channel(record['resource_id'])
                if channel:
                    try:
                        await changes.process(guild, channel, record['resource_key'], self.notify, security=True)
                    except (discord.HTTPException, ServerMessageError):
                        log.exception('Security repair retry failed guild=%s', guild.id)
            if guild and (record['status'] == 'pending' or not record.get('message_id') or record.get('delivered_status') != record['status']):
                await self.notify(guild, record)

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(ServerChanges(bot))
