"""GamerHQ Discord message capability adapter for portable Skills."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from typing import Any

import discord

from database import db
from services.url_service import validate_url
from skill_runtime.contracts.capabilities import SkillCapability
from .skill_host import CapabilityPermissions, _valid_identity


class SkillDiscordError(RuntimeError):
    """Safe public error at the Skill↔Discord boundary."""


def _discord_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _text(value: Any, *, label: str, maximum: int, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    if not allow_empty and not value:
        raise ValueError(f"{label} is required.")
    if _discord_length(value) > maximum:
        raise ValueError(f"{label} exceeds Discord's {maximum}-character limit.")
    return value


def _url(value: Any, *, label: str) -> str:
    value = _text(value, label=label, maximum=2048, allow_empty=False)
    try:
        validate_url(value)
    except Exception as exc:
        raise ValueError(f"{label} must be a safe HTTPS URL.") from exc
    return value


def _embed(config: Mapping[str, Any] | None) -> discord.Embed | None:
    if config is None:
        return None
    if not isinstance(config, Mapping):
        raise ValueError("Embed configuration must be an object.")

    allowed = {"title", "description", "url", "color", "footer", "thumbnail", "image", "fields"}
    unknown = set(config) - allowed
    if unknown:
        raise ValueError("Unsupported embed fields: " + ", ".join(sorted(unknown)))

    title = _text(config.get("title", ""), label="Embed title", maximum=256)
    description = _text(config.get("description", ""), label="Embed description", maximum=4096)
    url = _url(config["url"], label="Embed URL") if config.get("url") else None

    colour = None
    if "color" in config and config["color"] is not None:
        value = config["color"]
        if not isinstance(value, int) or isinstance(value, bool) or value not in range(0x1000000):
            raise ValueError("Embed color must be an integer from 0 to 16777215.")
        colour = discord.Colour(value)

    embed = discord.Embed(
        title=title or None,
        description=description or None,
        url=url,
        colour=colour,
    )

    footer = config.get("footer")
    if footer is not None:
        if not isinstance(footer, Mapping) or set(footer) - {"text", "iconUrl"}:
            raise ValueError("Embed footer must contain only text/iconUrl.")
        footer_text = _text(str(footer.get("text", "")), label="Embed footer", maximum=2048)
        icon = _url(footer["iconUrl"], label="Embed footer icon URL") if footer.get("iconUrl") else None
        if footer_text or icon:
            embed.set_footer(text=footer_text or None, icon_url=icon)

    if config.get("thumbnail"):
        embed.set_thumbnail(url=_url(config["thumbnail"], label="Embed thumbnail URL"))
    if config.get("image"):
        embed.set_image(url=_url(config["image"], label="Embed image URL"))

    fields = config.get("fields", ())
    if not isinstance(fields, (list, tuple)) or len(fields) > 25:
        raise ValueError("Embed fields must be a list with at most 25 items.")
    for item in fields:
        if not isinstance(item, Mapping) or set(item) - {"name", "value", "inline"}:
            raise ValueError("Embed field must contain only name/value/inline.")
        name = _text(item.get("name", ""), label="Embed field name", maximum=256, allow_empty=False)
        value = _text(item.get("value", ""), label="Embed field value", maximum=1024, allow_empty=False)
        inline = item.get("inline", False)
        if type(inline) is not bool:
            raise ValueError("Embed field inline must be true/false.")
        embed.add_field(name=name, value=value, inline=inline)

    if len(embed) > 6000:
        raise ValueError("Embed exceeds Discord's total 6000-character limit.")
    if not embed.title and not embed.description and not embed.fields and not embed.image.url and not embed.thumbnail.url:
        raise ValueError("Embed must contain visible content.")
    return embed


class GamerHQDiscordPort:
    """Guild + Skill scoped Discord message capability.

    Skills never receive the raw bot/client. Message edit/delete operations are
    additionally restricted to messages recorded as owned by that Skill.
    """

    def __init__(self, *, guild: discord.Guild, skill_id: str, permissions: CapabilityPermissions):
        _valid_identity(guild.id, skill_id)
        self.guild = guild
        self.skill_id = skill_id
        self.permissions = permissions

    def _channel(self, channel_id: int):
        if not isinstance(channel_id, int) or isinstance(channel_id, bool) or channel_id <= 0:
            raise ValueError("channel_id must be a positive Discord ID.")
        channel = self.guild.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise SkillDiscordError("Target text channel is unavailable.")
        if channel.guild.id != self.guild.id:
            raise SkillDiscordError("Target channel belongs to another server.")
        return channel

    def _check_send_access(self, channel, *, embed: bool = False) -> None:
        member = self.guild.me
        if member is None:
            raise SkillDiscordError("GamerHQ member state is unavailable.")
        rights = channel.permissions_for(member)
        if not rights.view_channel or not rights.send_messages:
            raise SkillDiscordError("GamerHQ cannot send messages in that channel.")
        if embed and not rights.embed_links:
            raise SkillDiscordError("GamerHQ cannot send embeds in that channel.")

    def _mentions(self, config: Mapping[str, Any] | None) -> discord.AllowedMentions:
        if config is None:
            return discord.AllowedMentions.none()
        if not isinstance(config, Mapping) or set(config) - {"everyone", "users", "roles"}:
            raise ValueError("Allowed mentions supports only everyone/users/roles.")

        everyone = config.get("everyone", False)
        if type(everyone) is not bool:
            raise ValueError("everyone mention setting must be true/false.")
        if everyone:
            self.permissions.require(SkillCapability.DISCORD_MENTIONS_EVERYONE.value)

        user_ids = config.get("users", ())
        role_ids = config.get("roles", ())
        if not isinstance(user_ids, (list, tuple)) or len(user_ids) > 25:
            raise ValueError("users mentions must contain at most 25 member IDs.")
        if not isinstance(role_ids, (list, tuple)) or len(role_ids) > 25:
            raise ValueError("roles mentions must contain at most 25 role IDs.")

        users = []
        for value in user_ids:
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("Mentioned user IDs must be positive integers.")
            member = self.guild.get_member(value)
            if member is None:
                raise ValueError("Mentioned user must be a current cached server member.")
            users.append(member)

        roles = []
        for value in role_ids:
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("Mentioned role IDs must be positive integers.")
            role = self.guild.get_role(value)
            if role is None or role.is_default():
                raise ValueError("Mentioned role must be a current non-everyone server role.")
            roles.append(role)

        return discord.AllowedMentions(
            everyone=everyone,
            users=users,
            roles=roles,
            replied_user=False,
        )

    def _owned(self, *, channel_id: int, message_id: int) -> bool:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM skill_discord_messages
                WHERE guild_id=? AND skill_id=? AND channel_id=? AND message_id=?
                """,
                (self.guild.id, self.skill_id, channel_id, message_id),
            ).fetchone()
        return row is not None

    def _forget(self, message_id: int) -> None:
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM skill_discord_messages WHERE guild_id=? AND skill_id=? AND message_id=?",
                (self.guild.id, self.skill_id, message_id),
            )

    def _remember(self, *, channel_id: int, message_id: int) -> None:
        now = int(time.time())
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_discord_messages(
                    guild_id,skill_id,channel_id,message_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (self.guild.id, self.skill_id, channel_id, message_id, now, now),
            )

    def _validate_message(self, *, content: str | None, embed: Mapping[str, Any] | None):
        if content is None and embed is None:
            raise ValueError("A message needs content or an embed.")
        if content is not None:
            content = _text(content, label="Message content", maximum=2000)
        rendered_embed = None
        if embed is not None:
            self.permissions.require(SkillCapability.DISCORD_EMBEDS_SEND.value)
            rendered_embed = _embed(embed)
        return content, rendered_embed

    async def send_message(
        self,
        *,
        channel_id: int,
        content: str | None = None,
        embed: Mapping[str, Any] | None = None,
        allowed_mentions: Mapping[str, Any] | None = None,
    ) -> int:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_SEND.value)
        channel = self._channel(channel_id)
        content, rendered_embed = self._validate_message(content=content, embed=embed)
        self._check_send_access(channel, embed=rendered_embed is not None)
        mentions = self._mentions(allowed_mentions)

        try:
            message = await channel.send(
                content=content,
                embed=rendered_embed,
                allowed_mentions=mentions,
            )
        except discord.HTTPException as exc:
            raise SkillDiscordError("Discord message send failed.") from exc

        try:
            self._remember(channel_id=channel.id, message_id=message.id)
        except sqlite3.Error as exc:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            raise SkillDiscordError("Message delivery could not be safely recorded.") from exc
        return message.id

    async def edit_own_message(
        self,
        *,
        channel_id: int,
        message_id: int,
        content: str | None = None,
        embed: Mapping[str, Any] | None = None,
    ) -> None:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_EDIT_OWN.value)
        if not self._owned(channel_id=channel_id, message_id=message_id):
            raise PermissionError("Skill does not own this Discord message.")
        channel = self._channel(channel_id)
        content, rendered_embed = self._validate_message(content=content, embed=embed)
        self._check_send_access(channel, embed=rendered_embed is not None)
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound as exc:
            self._forget(message_id)
            raise SkillDiscordError("Owned Discord message no longer exists.") from exc
        except discord.HTTPException as exc:
            raise SkillDiscordError("Owned Discord message could not be inspected.") from exc

        if self.guild.me is None or message.author.id != self.guild.me.id:
            self._forget(message_id)
            raise PermissionError("Recorded Skill message is no longer owned by GamerHQ.")

        try:
            await message.edit(
                content=content,
                embed=rendered_embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.NotFound as exc:
            self._forget(message_id)
            raise SkillDiscordError("Owned Discord message no longer exists.") from exc
        except discord.HTTPException as exc:
            raise SkillDiscordError("Discord message edit failed.") from exc

    async def delete_own_message(self, *, channel_id: int, message_id: int) -> None:
        self.permissions.require(SkillCapability.DISCORD_MESSAGES_DELETE_OWN.value)
        if not self._owned(channel_id=channel_id, message_id=message_id):
            raise PermissionError("Skill does not own this Discord message.")
        channel = self._channel(channel_id)
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            self._forget(message_id)
            return
        except discord.HTTPException as exc:
            raise SkillDiscordError("Owned Discord message could not be inspected.") from exc

        if self.guild.me is None or message.author.id != self.guild.me.id:
            self._forget(message_id)
            raise PermissionError("Recorded Skill message is no longer owned by GamerHQ.")

        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            raise SkillDiscordError("Discord message delete failed.") from exc
        finally:
            self._forget(message_id)
