"""Discord.py adapter behind the portable SkillContext Discord port."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import discord

from skill_runtime.contracts.capabilities import SkillCapability
from .skill_host import CapabilityPermissions


class DiscordResourcePolicy(Protocol):
    """Host-owned resource scope in addition to manifest capabilities."""

    def allows(self, *, guild_id: int, skill_id: str, channel_id: int, operation: str) -> bool: ...


class DenyAllDiscordPolicy:
    """Safe default until a host explicitly grants concrete resource scope."""

    def allows(self, *, guild_id: int, skill_id: str, channel_id: int, operation: str) -> bool:
        return False


class GamerHQDiscordPort:
    """Least-privilege Discord operations exposed to a Skill.

    A declared capability is necessary but not sufficient: the host resource
    policy must also allow the concrete destination.
    """

    def __init__(self, *, guild, skill_id: str, permissions: CapabilityPermissions,
                 policy: DiscordResourcePolicy | None = None):
        self.guild = guild
        self.skill_id = skill_id
        self.permissions = permissions
        self.policy = policy or DenyAllDiscordPolicy()

    def _channel(self, channel_id: int, operation: str):
        if not isinstance(channel_id, int) or channel_id <= 0:
            raise ValueError("channel_id must be positive.")
        channel = self.guild.get_channel(channel_id)
        if channel is None or getattr(channel, "guild", None) != self.guild:
            raise ValueError("Discord channel is not available in this guild.")
        if not self.policy.allows(
            guild_id=self.guild.id,
            skill_id=self.skill_id,
            channel_id=channel_id,
            operation=operation,
        ):
            raise PermissionError("Skill is not allowed to use this Discord resource.")
        return channel

    @staticmethod
    def _embed(value: Mapping[str, Any] | None):
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("Embed configuration must be an object.")
        # Discord.py performs the final schema validation. Limit the public
        # adapter to a bounded, provider-neutral subset used by Skills.
        allowed = {"title", "description", "url", "colour", "color"}
        if set(value) - allowed:
            raise ValueError("Unsupported embed field.")
        title = value.get("title")
        description = value.get("description")
        url = value.get("url")
        colour = value.get("colour", value.get("color"))
        if title is not None and (not isinstance(title, str) or len(title) > 256):
            raise ValueError("Embed title is invalid.")
        if description is not None and (not isinstance(description, str) or len(description) > 4096):
            raise ValueError("Embed description is invalid.")
        if url is not None and not isinstance(url, str):
            raise ValueError("Embed URL is invalid.")
        if colour is not None:
            if isinstance(colour, bool) or not isinstance(colour, int) or not 0 <= colour <= 0xFFFFFF:
                raise ValueError("Embed colour is invalid.")
        return discord.Embed(
            title=title,
            description=description,
            url=url,
            colour=discord.Colour(colour) if colour is not None else None,
        )

    def _mentions(self, value: Mapping[str, Any] | None, *, channel_id: int) -> discord.AllowedMentions:
        if value is None:
            return discord.AllowedMentions.none()
        if not isinstance(value, Mapping) or set(value) - {"users", "roles", "everyone"}:
            raise ValueError("Allowed mentions configuration is invalid.")
        users = bool(value.get("users", False))
        roles = bool(value.get("roles", False))
        everyone = bool(value.get("everyone", False))
        if users:
            self.permissions.require(SkillCapability.DISCORD_MENTIONS_USERS.value)
            if not self.policy.allows(guild_id=self.guild.id, skill_id=self.skill_id, channel_id=channel_id, operation="mentions.users"):
                raise PermissionError("Skill is not allowed to mention users in this Discord resource.")
        if roles:
            self.permissions.require(SkillCapability.DISCORD_MENTIONS_ROLES.value)
            if not self.policy.allows(guild_id=self.guild.id, skill_id=self.skill_id, channel_id=channel_id, operation="mentions.roles"):
                raise PermissionError("Skill is not allowed to mention roles in this Discord resource.")
        if everyone:
            self.permissions.require(SkillCapability.DISCORD_MENTIONS_EVERYONE.value)
            if not self.policy.allows(guild_id=self.guild.id, skill_id=self.skill_id, channel_id=channel_id, operation="mentions.everyone"):
                raise PermissionError("Skill is not allowed to mention everyone in this Discord resource.")
        return discord.AllowedMentions(users=users, roles=roles, everyone=everyone, replied_user=False)

    async def send_message(self, *, channel_id: int, content: str | None = None,
                           embed: Mapping[str, Any] | None = None,
                           allowed_mentions: Mapping[str, Any] | None = None) -> int:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_SEND.value)
        channel = self._channel(channel_id, "messages.send")
        if embed is not None:
            self.permissions.require(SkillCapability.DISCORD_EMBEDS_SEND.value)
        if content is not None and (not isinstance(content, str) or len(content) > 2000):
            raise ValueError("Message content is invalid.")
        if content is None and embed is None:
            raise ValueError("Message content or embed is required.")
        message = await channel.send(
            content=content,
            embed=self._embed(embed),
            allowed_mentions=self._mentions(allowed_mentions, channel_id=channel_id),
        )
        return int(message.id)

    async def edit_own_message(self, *, channel_id: int, message_id: int,
                               content: str | None = None,
                               embed: Mapping[str, Any] | None = None) -> None:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value)
        channel = self._channel(channel_id, "messages.edit_own")
        if embed is not None:
            self.permissions.require(SkillCapability.DISCORD_EMBEDS_SEND.value)
        if not isinstance(message_id, int) or message_id <= 0:
            raise ValueError("message_id must be positive.")
        if content is not None and (not isinstance(content, str) or len(content) > 2000):
            raise ValueError("Message content is invalid.")
        message = await channel.fetch_message(message_id)
        if self.guild.me is None or message.author.id != self.guild.me.id:
            raise PermissionError("Skill may edit only messages owned by this bot.")
        await message.edit(
            content=content,
            embed=self._embed(embed),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def delete_own_message(self, *, channel_id: int, message_id: int) -> None:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value)
        channel = self._channel(channel_id, "messages.delete_own")
        if not isinstance(message_id, int) or message_id <= 0:
            raise ValueError("message_id must be positive.")
        message = await channel.fetch_message(message_id)
        if self.guild.me is None or message.author.id != self.guild.me.id:
            raise PermissionError("Skill may delete only messages owned by this bot.")
        await message.delete()
