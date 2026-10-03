"""Controlled Discord capability adapter for portable Skills."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import discord

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.discord import (
    SkillDiscordError,
    SkillDiscordNotFound,
    SkillDiscordOperationFailed,
    SkillDiscordPermissionDenied,
)
from .skill_host import CapabilityPermissions


def _discord_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


class GamerHQDiscordAdapter:
    """Guild-scoped Discord operations exposed to Skills.

    Skills never receive the raw bot/client through the public SDK context.
    """

    def __init__(self, *, guild: discord.Guild, permissions: CapabilityPermissions):
        if guild is None or guild.id <= 0:
            raise ValueError("A valid guild is required.")
        self.guild = guild
        self.permissions = permissions

    def _channel(self, channel_id: int):
        getter = getattr(self.guild, "get_channel_or_thread", None)
        channel = getter(int(channel_id)) if getter else self.guild.get_channel(int(channel_id))
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise SkillDiscordNotFound("Target text channel is unavailable.")
        if channel.guild.id != self.guild.id:
            raise SkillDiscordError("Target channel belongs to another guild.")
        return channel

    def _effective(self, channel):
        member = self.guild.me
        if member is None:
            raise SkillDiscordError("Bot membership is unavailable.")
        rights = channel.permissions_for(member)
        if not rights.view_channel:
            raise SkillDiscordPermissionDenied("GamerHQ cannot view the target channel.")
        send = rights.send_messages_in_threads if isinstance(channel, discord.Thread) else rights.send_messages
        if not send:
            raise SkillDiscordPermissionDenied("GamerHQ cannot send messages in the target channel.")
        return rights

    def _content(self, content: str | None) -> str | None:
        if content is None:
            return None
        if not isinstance(content, str) or _discord_length(content) > 2000:
            raise SkillDiscordError("Message content must fit Discord's 2000-character limit.")
        return content

    def _embed(self, value: Mapping[str, Any] | None) -> discord.Embed | None:
        if value is None:
            return None
        self.permissions.require(SkillCapability.DISCORD_EMBEDS_SEND.value)
        if not isinstance(value, Mapping):
            raise SkillDiscordError("Embed configuration must be an object.")
        try:
            embed = discord.Embed.from_dict(dict(value))
        except (TypeError, ValueError) as exc:
            raise SkillDiscordError("Embed configuration is invalid.") from exc
        if len(embed) > 6000:
            raise SkillDiscordError("Embed content exceeds Discord limits.")
        return embed

    def _mentions(self, value: Mapping[str, Any] | None, rights) -> discord.AllowedMentions:
        if value is None:
            return discord.AllowedMentions.none()
        if not isinstance(value, Mapping) or set(value) - {"users", "roles", "everyone"}:
            raise SkillDiscordError("Allowed mentions configuration is invalid.")

        everyone = value.get("everyone", False)
        if type(everyone) is not bool:
            raise SkillDiscordError("Allowed mentions configuration is invalid.")
        if everyone:
            self.permissions.require(SkillCapability.DISCORD_MENTIONS_EVERYONE.value)
            if not getattr(rights, "mention_everyone", False):
                raise SkillDiscordPermissionDenied("GamerHQ cannot mention everyone in the target channel.")

        def ids(name):
            raw = value.get(name, ())
            if raw is None:
                return ()
            if not isinstance(raw, (list, tuple)) or any(type(item) is not int or item <= 0 for item in raw):
                raise SkillDiscordError("Allowed mentions configuration is invalid.")
            return tuple(dict.fromkeys(raw))

        users = []
        for user_id in ids("users"):
            member = self.guild.get_member(user_id)
            if member is None:
                raise SkillDiscordError("An allowed user mention is unavailable.")
            users.append(member)

        roles = []
        for role_id in ids("roles"):
            role = self.guild.get_role(role_id)
            if role is None:
                raise SkillDiscordError("An allowed role mention is unavailable.")
            roles.append(role)

        return discord.AllowedMentions(
            everyone=everyone,
            users=users if users else False,
            roles=roles if roles else False,
            replied_user=False,
        )

    async def send_message(
        self,
        *,
        channel_id: int,
        content: str | None = None,
        embed: Mapping[str, Any] | None = None,
        allowed_mentions: Mapping[str, Any] | None = None,
    ) -> int:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_SEND.value)
        content = self._content(content)
        embed_value = self._embed(embed)
        if content is None and embed_value is None:
            raise SkillDiscordError("A message needs content or an embed.")
        channel = self._channel(channel_id)
        rights = self._effective(channel)
        if embed_value is not None and not rights.embed_links:
            raise SkillDiscordPermissionDenied("GamerHQ cannot send embeds in the target channel.")
        mentions = self._mentions(allowed_mentions, rights)
        try:
            message = await channel.send(
                content=content,
                embed=embed_value,
                allowed_mentions=mentions,
            )
        except discord.Forbidden as exc:
            raise SkillDiscordPermissionDenied("Discord message delivery was denied.") from exc
        except discord.NotFound as exc:
            raise SkillDiscordNotFound("Target channel is unavailable.") from exc
        except discord.HTTPException as exc:
            raise SkillDiscordOperationFailed("Discord message delivery failed.") from exc
        return int(message.id)

    async def _owned_message(self, *, channel_id: int, message_id: int):
        channel = self._channel(channel_id)
        self._effective(channel)
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound as exc:
            raise SkillDiscordNotFound("Managed Skill message is unavailable.") from exc
        except discord.Forbidden as exc:
            raise SkillDiscordPermissionDenied("Managed Skill message access was denied.") from exc
        except discord.HTTPException as exc:
            raise SkillDiscordOperationFailed("Managed Skill message lookup failed.") from exc
        if self.guild.me is None or message.author.id != self.guild.me.id:
            raise SkillDiscordError("Skills may edit or delete only GamerHQ-authored messages.")
        return channel, message

    async def edit_own_message(
        self,
        *,
        channel_id: int,
        message_id: int,
        content: str | None = None,
        embed: Mapping[str, Any] | None = None,
        allowed_mentions: Mapping[str, Any] | None = None,
    ) -> None:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value)
        content = self._content(content)
        embed_value = self._embed(embed)
        if content is None and embed_value is None:
            raise SkillDiscordError("A message needs content or an embed.")
        channel, message = await self._owned_message(channel_id=channel_id, message_id=message_id)
        rights = self._effective(channel)
        if embed_value is not None and not rights.embed_links:
            raise SkillDiscordError("GamerHQ cannot send embeds in the target channel.")
        mentions = self._mentions(allowed_mentions, rights)
        try:
            await message.edit(content=content, embed=embed_value, allowed_mentions=mentions)
        except discord.Forbidden as exc:
            raise SkillDiscordPermissionDenied("Discord message update was denied.") from exc
        except discord.NotFound as exc:
            raise SkillDiscordNotFound("Managed Skill message is unavailable.") from exc
        except discord.HTTPException as exc:
            raise SkillDiscordOperationFailed("Discord message update failed.") from exc

    async def delete_own_message(self, *, channel_id: int, message_id: int) -> None:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value)
        _, message = await self._owned_message(channel_id=channel_id, message_id=message_id)
        try:
            await message.delete()
        except discord.Forbidden as exc:
            raise SkillDiscordPermissionDenied("Discord message deletion was denied.") from exc
        except discord.NotFound as exc:
            raise SkillDiscordNotFound("Managed Skill message is unavailable.") from exc
        except discord.HTTPException as exc:
            raise SkillDiscordOperationFailed("Discord message deletion failed.") from exc
