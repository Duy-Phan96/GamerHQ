import logging
import discord
from discord.ext import commands

from database import db
from services.music_bot_service import with_music_access
from services.game_area_safety import area_lock
from services.temp_voice_service import empty_cleanup, forget, restrict_legacy_owner


GLOBAL_VOICE_CATEGORY = "🔊 VOICE CHANNELS"
GLOBAL_CREATE_VOICE = "➕ Create Voice"
LEGACY_GLOBAL_VOICES = {"🎮 Gaming 1", "🎮 Gaming 2"}


class VoiceGenerator(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _global_category(guild: discord.Guild):
        return next(
            (c for c in guild.categories if c.name.casefold() == GLOBAL_VOICE_CATEGORY.casefold()),
            None,
        )

    async def ensure_global_voice_structure(self, guild: discord.Guild):
        """Keep the public GamerHQ voice area aligned with the agreed blueprint."""
        category = self._global_category(guild)
        if category is None:
            return

        generator = next(
            (
                c for c in category.voice_channels
                if c.name.casefold() == GLOBAL_CREATE_VOICE.casefold()
            ),
            None,
        )
        if generator is None:
            try:
                await guild.create_voice_channel(
                    GLOBAL_CREATE_VOICE,
                    category=category,
                    reason="GamerHQ global Create Voice generator",
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        # Gaming 1 / Gaming 2 were the old static rooms. The user explicitly
        # replaced them with the global Create Voice generator. Never kick
        # users out: an occupied legacy room is left alone until it is empty.
        for channel in list(category.voice_channels):
            if channel.name in LEGACY_GLOBAL_VOICES and not channel.members:
                try:
                    await channel.delete(reason="Replaced by GamerHQ global Create Voice")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def cleanup_empty_temp_channels(self, guild):
        for channel_id in db.get_temp_voice_ids():
            channel = guild.get_channel(channel_id)
            if channel is None:
                logging.getLogger(__name__).warning('Temporary voice %s absent from guild %s cache; retained for /server health review.', channel_id, guild.id)
                continue
            try:
                if isinstance(channel, discord.VoiceChannel) and not channel.members:
                    await empty_cleanup(channel)
                elif isinstance(channel, discord.VoiceChannel):
                    await restrict_legacy_owner(channel)
            except discord.HTTPException:
                logging.getLogger(__name__).exception('Temporary voice recovery failed channel=%s; tracking retained.', channel_id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if db.get_temp_voice(channel.id):
            forget(channel.id)

    async def _create_global_temp_voice(self, member: discord.Member, generator: discord.VoiceChannel):
        category = generator.category
        bot_member = member.guild.me
        overwrites = dict(category.overwrites) if isinstance(category, discord.CategoryChannel) else {}
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            manage_channels=False,
            move_members=False,
        )
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                manage_channels=True,
                move_members=True,
            )

        overwrites = with_music_access(member.guild, overwrites, 'voice', parent=category)
        temp = await member.guild.create_voice_channel(
            name=f"🔊 {member.display_name}'s Room",
            category=category,
            overwrites=overwrites,
            reason="GamerHQ global temporary voice",
        )
        # game_id=0 marks a global temporary room; the existing table does not
        # enforce a foreign key and cleanup only needs channel_id/host_id.
        db.add_temp_voice(temp.id, member.id, 0)
        await member.move_to(temp, reason="GamerHQ global Create Voice")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return
        if member.bot:
            if before.channel and db.get_temp_voice(before.channel.id):
                await empty_cleanup(before.channel)
            return

        # Joined a Create Voice generator.
        if after.channel:
            # Global public generator in VOICE CHANNELS.
            if (
                after.channel.name.casefold() == GLOBAL_CREATE_VOICE.casefold()
                and after.channel.category
                and after.channel.category.name.casefold() == GLOBAL_VOICE_CATEGORY.casefold()
            ):
                await self._create_global_temp_voice(member, after.channel)
            else:
                # Optional Streamer-area Create Voice generator.
                streamer_profile = db.get_streamer_profile_by_create_voice(member.guild.id, after.channel.id)
                if streamer_profile:
                    streamer_cog = self.bot.get_cog("Streamer")
                    if streamer_cog:
                        temp = await streamer_cog.create_streamer_temp_voice(member.guild, member, streamer_profile)
                        if temp:
                            await member.move_to(temp, reason="GamerHQ Streamer Create Voice")
                    return

                # Per-game Create Voice generator.
                games = db.get_area_games()
                game = next(
                    (g for g in games if g["create_voice_channel_id"] == after.channel.id),
                    None,
                )
                if game:
                    async with area_lock(game["id"]):
                        game = db.get_game_by_id(game["id"])
                        if not game or not game.get("category_id") or not game.get("area_enabled"):
                            return
                        category = member.guild.get_channel(game["category_id"])
                        role = member.guild.get_role(game["role_id"])
                        bot_member = member.guild.me

                        overwrites = dict(category.overwrites) if isinstance(category, discord.CategoryChannel) else {}
                        if role:
                            overwrites[role] = discord.PermissionOverwrite(
                                view_channel=True, connect=True, speak=True
                            )
                        overwrites[member] = discord.PermissionOverwrite(
                            view_channel=True,
                            connect=True,
                            speak=True,
                            manage_channels=False,
                            move_members=False,
                        )
                        if bot_member:
                            overwrites[bot_member] = discord.PermissionOverwrite(
                                view_channel=True,
                                connect=True,
                                manage_channels=True,
                                move_members=True,
                            )

                        overwrites = with_music_access(member.guild, overwrites, 'voice', parent=category)
                        temp = await member.guild.create_voice_channel(
                            name=f"🔊 {member.display_name}'s Party",
                            category=category if isinstance(category, discord.CategoryChannel) else None,
                            overwrites=overwrites,
                            reason=f"GamerHQ temporary voice for {game['name']}",
                        )
                        db.add_temp_voice(temp.id, member.id, game["id"])
                        await member.move_to(temp, reason="GamerHQ Create Voice")

        # Remove any tracked temporary channel once empty.
        if before.channel and before.channel.id in set(db.get_temp_voice_ids()):
            if len(before.channel.members) == 0:
                await empty_cleanup(before.channel)

        # If one of the old Gaming rooms was occupied during startup, remove it
        # automatically after the final member leaves.
        if (
            before.channel
            and before.channel.name in LEGACY_GLOBAL_VOICES
            and before.channel.category
            and before.channel.category.name.casefold() == GLOBAL_VOICE_CATEGORY.casefold()
            and len(before.channel.members) == 0
        ):
            try:
                await before.channel.delete(reason="Replaced by GamerHQ global Create Voice")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def setup(bot):
    await bot.add_cog(VoiceGenerator(bot))
