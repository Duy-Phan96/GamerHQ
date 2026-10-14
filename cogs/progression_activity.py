"""GamerHQ host adapter for portable Progression activity awards."""
from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands, tasks

from database import db

log = logging.getLogger(__name__)

GET_CONFIG_API = "progression.get-config.v1"
RECORD_ACTIVITY_API = "progression.record-activity.v1"
RECORD_SIGNAL_API = "progression.record-signal.v1"


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


def _member_role_ids(member) -> set[int]:
    return {
        int(role.id)
        for role in getattr(member, "roles", ())
        if getattr(role, "id", None) is not None
    }


def _selectable_game_role_ids() -> set[int]:
    role_ids: set[int] = set()
    for game in db.get_selectable_games():
        try:
            role_id = int(game.get("role_id"))
        except (TypeError, ValueError):
            continue
        if role_id > 0:
            role_ids.add(role_id)
    return role_ids


def progression_member_signals(before, after, *, now: int | None = None) -> tuple[dict, ...]:
    """Translate trusted Discord/member transitions into public Progression signals."""
    if getattr(before, "guild", None) is None or getattr(after, "guild", None) is None:
        return ()
    if before.guild.id != after.guild.id or before.id != after.id:
        return ()

    guild = after.guild
    before_roles = _member_role_ids(before)
    after_roles = _member_role_ids(after)
    signals: list[dict] = []

    booster_role = getattr(guild, "premium_subscriber_role", None)
    booster_role_id = getattr(booster_role, "id", None)
    if (
        booster_role_id is not None
        and int(booster_role_id) not in before_roles
        and int(booster_role_id) in after_roles
    ):
        premium_since = getattr(after, "premium_since", None)
        if premium_since is not None and hasattr(premium_since, "timestamp"):
            occurred_at = int(premium_since.timestamp())
            signals.append({
                "memberId": int(after.id),
                "provider": "discord",
                "signal": "server_boosted",
                "dedupeKey": (
                    f"discord:server_boosted:{guild.id}:{after.id}:{occurred_at}"
                ),
                "occurredAt": occurred_at,
            })

    game_role_ids = _selectable_game_role_ids()
    if game_role_ids:
        before_games = before_roles & game_role_ids
        after_games = after_roles & game_role_ids
        if not before_games and after_games:
            occurred_at = int(now if now is not None else time.time())
            signals.append({
                "memberId": int(after.id),
                "provider": "profile",
                "signal": "first_game_added",
                "dedupeKey": f"profile:first_game_added:{guild.id}:{after.id}",
                "occurredAt": occurred_at,
            })

    return tuple(signals)


async def emit_progression_signal(bot, guild_id: int, payload: dict, *, attempts: int = 2) -> bool:
    """Best-effort public Runtime call; core member flows never depend on Progression."""
    runtime = getattr(bot, "skill_runtime", None) if bot is not None else None
    if runtime is None:
        return False

    try:
        status = await runtime.status(guild_id=guild_id, skill_id="progression")
    except Exception:
        log.debug(
            "Progression signal skipped because Skill status is unavailable guild=%s",
            guild_id,
            exc_info=True,
        )
        return False

    if not getattr(status, "enabled", False):
        return False

    attempts = max(1, min(int(attempts), 3))
    for attempt in range(attempts):
        try:
            await runtime.call_management(
                guild_id=guild_id,
                skill_id="progression",
                contract_id=RECORD_SIGNAL_API,
                payload=dict(payload),
            )
            return True
        except Exception:
            if attempt + 1 >= attempts:
                log.exception(
                    "Progression signal delivery failed guild=%s provider=%s signal=%s",
                    guild_id,
                    payload.get("provider"),
                    payload.get("signal"),
                )
    return False


async def dispatch_member_progression_signals(bot, before, after, *, now: int | None = None) -> tuple[dict, ...]:
    """Observe one member update and best-effort deliver every derived signal."""
    payloads = progression_member_signals(before, after, now=now)
    for payload in payloads:
        await emit_progression_signal(bot, after.guild.id, payload)
    return payloads


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

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        await dispatch_member_progression_signals(self.bot, before, after)

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


async def award_completed_lfg(bot, guild: discord.Guild, event: dict) -> None:
    """Award completion XP once per event/member using stable dedupe keys."""
    runtime = getattr(bot, "skill_runtime", None) if bot is not None else None
    if runtime is None:
        return
    try:
        status = await runtime.status(guild_id=guild.id, skill_id="progression")
        if not status.enabled:
            return
    except Exception:
        return

    occurred_at = int(event.get("ended_at") or time.time())
    event_id = int(event["id"])
    host_id = int(event["host_id"])
    joined = {
        int(row["user_id"])
        for row in db.get_lfg_event_members(event_id)
        if row["status"] == "joined"
    }
    joined.add(host_id)

    async def record(member_id: int, source: str):
        try:
            await runtime.call_management(
                guild_id=guild.id,
                skill_id="progression",
                contract_id=RECORD_ACTIVITY_API,
                payload={
                    "memberId": member_id,
                    "source": source,
                    "units": 1,
                    "occurredAt": occurred_at,
                    "dedupeKey": f"lfg:{event_id}:{source}:{member_id}",
                },
            )
        except Exception:
            log.exception(
                "Progression event award failed guild=%s event=%s member=%s source=%s",
                guild.id, event_id, member_id, source,
            )

    for member_id in sorted(joined):
        await record(member_id, "lfgParticipation")
    await record(host_id, "eventHost")


async def setup(bot: commands.Bot):
    await bot.add_cog(ProgressionActivity(bot))
