"""Discord.py adapter for portable Skill Runtime Discord capabilities.

The adapter is scoped to one guild + Skill identity. Portable Skill code never
receives a Discord client, Guild, Channel or Message object through this API.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import discord

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.context import DiscordChannelInfo
from skill_runtime.contracts.errors import (
    CapabilityUnavailableError,
    HostPermissionDeniedError,
    InvalidHostOperationError,
    ResourceNotFoundError,
    TransientHostError,
)

from .skill_host import CapabilityPermissions, _valid_identity


DISCORD_HOST_CAPABILITIES = frozenset({
    SkillCapability.DISCORD_CHANNELS_READ.value,
    SkillCapability.DISCORD_MESSAGES_SEND.value,
    SkillCapability.DISCORD_EMBEDS_SEND.value,
})


def _channel_kind(channel: object) -> str:
    if isinstance(channel, discord.TextChannel):
        return "text"
    if isinstance(channel, discord.Thread):
        return "thread"
    if isinstance(channel, discord.ForumChannel):
        return "forum"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.StageChannel):
        return "stage"
    return "unknown"


def _translate_discord_error(exc: BaseException) -> BaseException:
    if isinstance(exc, discord.Forbidden):
        return HostPermissionDeniedError("Discord denied this Skill operation.")
    if isinstance(exc, discord.NotFound):
        return ResourceNotFoundError("The Discord resource is no longer available.")
    if isinstance(exc, discord.HTTPException):
        status = int(getattr(exc, "status", 0) or 0)
        if status == 429 or status >= 500:
            return TransientHostError("Discord is temporarily unavailable.")
        return InvalidHostOperationError("Discord rejected this Skill operation.")
    return TransientHostError("Discord operation failed.")


class GamerHQDiscordAdapter:
    """Narrow Discord capability adapter bound to exactly one guild + Skill."""

    def __init__(
        self,
        *,
        guild: discord.Guild,
        skill_id: str,
        permissions: CapabilityPermissions,
    ):
        _valid_identity(guild.id, skill_id)
        self.guild = guild
        self.guild_id = guild.id
        self.skill_id = skill_id
        self.permissions = permissions

    def _channel(self, channel_id: int):
        if int(channel_id) <= 0:
            raise InvalidHostOperationError("channel_id must be positive.")
        resolver = getattr(self.guild, "get_channel_or_thread", None)
        channel = resolver(int(channel_id)) if resolver else self.guild.get_channel(int(channel_id))
        if channel is None:
            raise ResourceNotFoundError("Discord channel was not found in this guild.")
        channel_guild = getattr(channel, "guild", None)
        if channel_guild is None or int(getattr(channel_guild, "id", 0)) != self.guild_id:
            raise ResourceNotFoundError("Discord channel was not found in this guild.")
        return channel

    async def get_channel(self, *, channel_id: int) -> DiscordChannelInfo:
        self.permissions.require(SkillCapability.DISCORD_CHANNELS_READ.value)
        channel = self._channel(channel_id)
        return DiscordChannelInfo(
            id=int(channel.id),
            name=str(getattr(channel, "name", ""))[:100],
            kind=_channel_kind(channel),
        )

    @staticmethod
    def _embed(value: Mapping[str, Any] | None) -> discord.Embed | None:
        if value is None:
            return None
        try:
            return discord.Embed.from_dict(dict(value))
        except (TypeError, ValueError, KeyError) as exc:
            raise InvalidHostOperationError("Embed payload is invalid.") from exc

    @staticmethod
    def _allowed_mentions(value: Mapping[str, Any] | None) -> discord.AllowedMentions:
        if value is None:
            return discord.AllowedMentions.none()
        supported = {"everyone", "users", "roles", "replied_user"}
        unknown = set(value) - supported
        if unknown:
            raise InvalidHostOperationError("allowed_mentions contains unsupported fields.")
        try:
            return discord.AllowedMentions(**{key: value[key] for key in value})
        except (TypeError, ValueError) as exc:
            raise InvalidHostOperationError("allowed_mentions is invalid.") from exc

    async def send_message(
        self,
        *,
        channel_id: int,
        content: str | None = None,
        embed: Mapping[str, Any] | None = None,
        allowed_mentions: Mapping[str, Any] | None = None,
    ) -> int:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_SEND.value)
        if embed is not None:
            self.permissions.require(SkillCapability.DISCORD_EMBEDS_SEND.value)
        if content is None and embed is None:
            raise InvalidHostOperationError("A message needs content or an embed.")
        if content is not None and len(content) > 2000:
            raise InvalidHostOperationError("Message content exceeds Discord's limit.")

        channel = self._channel(channel_id)
        if not hasattr(channel, "send"):
            raise InvalidHostOperationError("This Discord channel cannot receive messages.")
        try:
            message = await channel.send(
                content=content,
                embed=self._embed(embed),
                allowed_mentions=self._allowed_mentions(allowed_mentions),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            raise _translate_discord_error(exc) from exc
        except (TypeError, ValueError) as exc:
            raise InvalidHostOperationError("Discord message payload is invalid.") from exc
        return int(message.id)

    async def edit_own_message(
        self,
        *,
        channel_id: int,
        message_id: int,
        content: str | None = None,
        embed: Mapping[str, Any] | None = None,
    ) -> None:
        raise CapabilityUnavailableError(
            "Editing Skill-owned Discord messages is not provided by this host version."
        )

    async def delete_own_message(self, *, channel_id: int, message_id: int) -> None:
        raise CapabilityUnavailableError(
            "Deleting Skill-owned Discord messages is not provided by this host version."
        )
