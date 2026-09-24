"""Private button-only beta flow; no target/message/profile configuration."""
import asyncio
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

import discord
import config
from database import twitch
from services import streamer_hub_service as hub
from services.twitch_service import TwitchError


def connected_text(row):
    name = discord.utils.escape_markdown(discord.utils.escape_mentions(row['twitch_display_name']))
    return f'# 🎥 Streamer Hub — Beta\n\nTwitch\n✅ Connected as {name}\n\nLive notifications\n' + ('✅ Enabled' if row['notifications_enabled'] else '⚠️ Reconnect Twitch to resume')


class DisconnectView(discord.ui.View):
    def __init__(self, user_id, *, confirm=False):
        super().__init__(timeout=180)
        self.user_id, self.confirm = user_id, confirm
        button = discord.ui.Button(label='Confirm Disconnect' if confirm else 'Disconnect Twitch', style=discord.ButtonStyle.danger)
        button.callback = self.disconnect
        self.add_item(button)

    async def disconnect(self, interaction):
        if not interaction.guild or interaction.guild.id != config.GUILD_ID or interaction.user.id != self.user_id:
            await interaction.response.send_message('This connection belongs to another member.', ephemeral=True)
            return
        if not self.confirm:
            await interaction.response.edit_message(content='Disconnect Twitch and stop future live notifications?', view=DisconnectView(self.user_id, confirm=True))
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.client.twitch_hub.disconnect(interaction.guild, self.user_id)
        await interaction.edit_original_response(content='Twitch disconnected. Your Discord roles are unchanged.', view=None)
        self.stop()


class ConfirmConnection(discord.ui.View):
    def __init__(self, manager, key, nonce, account, tokens):
        super().__init__(timeout=180)
        self.manager, self.key, self.nonce = manager, key, nonce
        self.account, self.tokens = account, tokens
        self.deadline = time.monotonic() + 180

    async def on_timeout(self):
        self.tokens = None

    @discord.ui.button(label='Confirm Twitch Account', style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if not interaction.guild or (interaction.guild.id, interaction.user.id) != self.key or not await hub.authorize(interaction.guild, interaction.user.id):
            await interaction.response.send_message(hub.DENIED, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with self.manager.lock(self.key):
            if not self.tokens or time.monotonic() > self.deadline or self.manager.attempts.get(self.key) != self.nonce:
                await interaction.edit_original_response(content='Connection expired. Start Connect Twitch again.', view=None)
                return
            try:
                twitch.save(*self.key, self.account, self.tokens)
            except sqlite3.IntegrityError:
                await interaction.edit_original_response(content='This Discord or Twitch account is already connected. Disconnect the existing connection first.', view=None)
                self.tokens = None
                return
            self.tokens = None
            self.manager.attempts.pop(self.key, None)
            row = twitch.connection(*self.key)
            self.manager.launch(row)
        await interaction.edit_original_response(content=connected_text(row), view=DisconnectView(interaction.user.id))
        self.stop()


async def wait_for_device(interaction, manager, key, nonce, device):
    interval = max(5, int(device['interval']))
    deadline = time.monotonic() + min(int(device['expires_in']), 600)
    try:
        while time.monotonic() < deadline and manager.attempts.get(key) == nonce:
            await asyncio.sleep(interval)
            if not await hub.authorize(interaction.guild, interaction.user.id): break
            try:
                token = await manager.api.exchange(device['device_code'])
            except TwitchError as exc:
                if exc.code == 'authorization_pending': continue
                if exc.code == 'slow_down':
                    interval += 5
                    continue
                raise
            account = await manager.api.account(token['access_token'])
            if manager.attempts.get(key) != nonce: return
            view = ConfirmConnection(manager, key, nonce, account, token)
            name = discord.utils.escape_markdown(discord.utils.escape_mentions(account['display_name']))
            await interaction.edit_original_response(content=f'Connect Twitch account **{name}** to your GamerHQ account? Confirm only if this is your account.', view=view)
            return
        await interaction.edit_original_response(content='Connection expired or access changed. Start Connect Twitch again.', view=None)
    except asyncio.CancelledError:
        raise
    except Exception:
        await interaction.edit_original_response(content='Twitch connection unavailable. Please try again later.', view=None)
    finally:
        if manager.pending.get(key) is asyncio.current_task(): manager.pending.pop(key, None)


async def open_hub(interaction):
    if not await hub.authorize(interaction.guild, interaction.user.id):
        await interaction.response.send_message(hub.DENIED, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    manager = interaction.client.twitch_hub
    key = (interaction.guild.id, interaction.user.id)
    async with manager.lock(key):
        row = twitch.connection(*key)
        if row:
            await interaction.edit_original_response(content=connected_text(row), view=DisconnectView(interaction.user.id))
            return
        if key in manager.pending:
            await interaction.edit_original_response(content='A Twitch connection is already pending. Complete the earlier private prompt or wait for it to expire.')
            return
        try:
            device = await manager.api.device()
            url = urlsplit(device['verification_uri'])
            if url.scheme != 'https' or url.netloc != 'www.twitch.tv' or url.path != '/activate':
                raise ValueError('Unexpected activation endpoint')
            view = discord.ui.View(timeout=600)
            view.add_item(discord.ui.Button(label='Connect Twitch', emoji='🟣', url=device['verification_uri']))
            await interaction.edit_original_response(content='Authorize GamerHQ on Twitch, then confirm your Twitch account here. Do not share this private connection link.', view=view)
            nonce = secrets.token_urlsafe(32)
            manager.attempts[key] = nonce
            manager.pending[key] = asyncio.create_task(wait_for_device(interaction, manager, key, nonce, device))
        except Exception:
            await interaction.edit_original_response(content='Twitch connection unavailable. Please try again later.', view=None)


class HubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Connect Twitch', emoji='🟣', custom_id='gamerhq:twitch:connect')
    async def connect(self, interaction, button):
        await open_hub(interaction)
