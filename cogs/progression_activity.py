"""GamerHQ host adapter for portable Progression activity awards."""
from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)

GET_CONFIG_API = "progression.get-config.v1"
RECORD_ACTIVITY_API = "progression.record-activity.v1"


def eligible_voice_members(guild: discord.Guild, channel: discord.VoiceChannel) -> tuple[discord.Member, ...]:
    """Return humans eligible for one active voice minute.

    Voice XP requires at least two non-bot members and excludes the guild AFK
    channel. A member may be muted/deafened while gaming; presence with another
    human is the V1 activity signal.
    """
    if getattr(guild, "afk_channel", None) is channel:
        return ()
    humans = tuple(member for member in channel.members if not member.bot)
    return humans if len(humans) >= 2 else ()


class ProgressionActivity(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._voice_minutes: dict[tuple[int, int], int] = {}
        self.voice_sampler.start()

    def cog_unload(self):
        self.voice_sampler.cancel()

    async def _management(self, guild_id: int, contract_id: str, payload: dict):
        runtime = getattr(self.bot, "skill_runtime", None)
        if runtime is None:
            raise RuntimeError("Skill Runtime unavailable")
        return await runtime.call_management(
            guild_id=guild_id,
            skill_id="progression",
            contract_id=contract_id,
            payload=payload,
        )

    async def _enabled_config(self, guild: discord.Guild):
        runtime = getattr(self.bot, "skill_runtime", None)
        if runtime is None:
            return None
        try:
            status = await runtime.status(guild_id=guild.id, skill_id="progression")
            if not status.enabled:
                return None
            response = await self._management(guild.id, GET_CONFIG_API, {})
            return dict(response.get("config") or {})
        except Exception:
            log.exception("Progression config unavailable for guild=%s", guild.id)
            return None

    @tasks.loop(minutes=1)
    async def voice_sampler(self):
        for guild in tuple(self.bot.guilds):
            config = await self._enabled_config(guild)
            if not config:
                continue
            source = dict((config.get("xpSources") or {}).get("voice") or {})
            if not source.get("enabled"):
                continue
            window = max(1, int(source.get("windowMinutes", 10)))

            eligible_now: set[tuple[int, int]] = set()
            for channel in guild.voice_channels:
                for member in eligible_voice_members(guild, channel):
                    key = (guild.id, member.id)
                    eligible_now.add(key)
                    self._voice_minutes[key] = self._voice_minutes.get(key, 0) + 1
                    if self._voice_minutes[key] < window:
                        continue
                    self._voice_minutes[key] -= window
                    try:
                        await self._management(
                            guild.id,
                            RECORD_ACTIVITY_API,
                            {
                                "memberId": member.id,
                                "source": "voice",
                                "units": 1,
                                "occurredAt": int(time.time()),
                            },
                        )
                    except Exception:
                        # Preserve the completed window for retry on the next sample.
                        self._voice_minutes[key] += window
                        log.exception(
                            "Progression voice award failed guild=%s member=%s",
                            guild.id,
                            member.id,
                        )

            for key in tuple(self._voice_minutes):
                if key[0] == guild.id and key not in eligible_now:
                    self._voice_minutes.pop(key, None)

    @voice_sampler.before_loop
    async def before_voice_sampler(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ProgressionActivity(bot))
