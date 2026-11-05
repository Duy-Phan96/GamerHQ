"""Trusted, durable Discord resource adapter. Portable Skills receive no client.

Text resources have no delete operation. Ambiguous or uncertain remote effects
fail closed. Deployment-owned policy selects the only allowed parent category.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import re
import secrets
import time

import discord

from database import db
from skill_runtime.contracts.errors import CapabilityUnavailableError, InvalidHostOperationError, TransientHostError
from .skill_host import _valid_identity


def event_channel_identity(channel, *, guild_id, message_id=None, control_id=None):
    """Resolve only the stored channel, guild and ownership marker."""
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
        return None
    with db.connect() as conn:
        rows = conn.execute("SELECT resource_key,state_json FROM skill_managed_resources WHERE guild_id=? "
            "AND skill_id='event-manager' AND kind='text' AND json_extract(state_json,'$.channelId')=?",
            (guild_id, channel.id)).fetchall()
    if len(rows) != 1:
        return None
    key, raw = rows[0]
    state = json.loads(raw)
    if not key.startswith("event:") or state.get("marker") != channel.topic:
        return None
    if message_id is not None and state.get("messageId") != message_id:
        return None
    if control_id is not None and control_id != event_card_id(state["marker"]):
        return None
    return key.removeprefix("event:")


def event_card_id(marker):
    return "gamerhq:event:manage:" + hashlib.sha256(marker.encode()).hexdigest()[:32]


def event_card_controls(marker):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Manage", custom_id=event_card_id(marker)))
    return view


def has_publication_marker(message, marker):
    return (any(getattr(embed.footer, "text", None) == marker for embed in message.embeds) or
            any(getattr(button, "custom_id", None) == event_card_id(marker)
                for row in getattr(message, "components", ()) for button in row.children))


class GamerHQManagedResources:
    def __init__(self, *, guild, skill_id, permissions, policy=None, clock=time.time, user_lookup=None):
        _valid_identity(guild.id, skill_id)
        self.guild, self.skill_id, self.permissions = guild, skill_id, permissions
        self.policy = policy or {}
        self.clock = clock
        self.user_lookup = user_lookup

    def _policy(self, *, voice=False):
        self.permissions.require("discord.resources.manage")
        parent = self.guild.get_channel(int((self.policy.get("voiceCategoryId") if voice else None) or self.policy.get("categoryId") or 0))
        if not isinstance(parent, discord.CategoryChannel) or parent.guild.id != self.guild.id:
            raise CapabilityUnavailableError("Managed resource category must be configured by the Host.")
        rights = parent.permissions_for(self.guild.me)
        if not rights.manage_channels or not rights.view_channel or (not voice and (not rights.send_messages or not rights.read_message_history)):
            raise CapabilityUnavailableError("Host bot lacks the managed category permissions required by this capability.")
        return parent

    async def health(self):
        parent = self._policy()
        if self.policy.get("voiceCategoryId"):
            self._policy(voice=True)
        with db.connect() as conn:
            pending = conn.execute("SELECT 1 FROM skill_managed_resources WHERE guild_id=? AND skill_id=? AND "
                "(json_extract(state_json,'$.creating')=1 OR json_extract(state_json,'$.messagePending')=1 OR "
                "json_extract(state_json,'$.synchronizationPending')=1 OR json_extract(state_json,'$.retiring')=1) LIMIT 1",
                (self.guild.id, self.skill_id)).fetchone()
        if pending:
            return {"state": "DEGRADED", "detail": "Managed resource synchronization is pending or requires reconciliation."}
        return {"state": "PASS", "categoryId": str(parent.id)}

    @contextmanager
    def _claim(self, key, kind, *, reserve=True):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,110}", key):
            raise InvalidHostOperationError("Invalid managed resource key.")
        identity = (self.guild.id, self.skill_id, key)
        token = secrets.token_hex(16)
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM skill_managed_resources WHERE guild_id=? AND skill_id=? AND resource_key=?", identity).fetchone()
            if row and row["kind"] != kind:
                raise InvalidHostOperationError("Managed resource kind cannot change.")
            if row and row["lease_until"] > int(self.clock()):
                raise TransientHostError("Managed resource reconciliation is already in progress.")
            old_state = json.loads(row["state_json"]) if row else None
            if reserve and (row is None or (not old_state.get("channelId") and old_state.get("reserved") is False)):
                count = conn.execute("SELECT count(*) FROM skill_managed_resources WHERE guild_id=? AND skill_id=? "
                    "AND (json_extract(state_json,'$.channelId') IS NOT NULL OR coalesce(json_extract(state_json,'$.reserved'),1)=1)", identity[:2]).fetchone()[0]
                if count >= int(self.policy.get("maxResources", 100)):
                    raise InvalidHostOperationError("Managed resource quota reached.")
            state = old_state if row else {"marker": "skill-resource:" + secrets.token_hex(24), "members": {}, "reserved": True}
            if reserve:
                state["reserved"] = True
            elif row is None:
                state["reserved"] = False
            conn.execute("INSERT INTO skill_managed_resources VALUES(?,?,?,?,?,?,?) ON CONFLICT(guild_id,skill_id,resource_key) "
                         "DO UPDATE SET state_json=excluded.state_json,lease_token=excluded.lease_token,lease_until=excluded.lease_until",
                         (*identity, kind, json.dumps(state), token, int(self.clock()) + 120))
        try:
            yield state, lambda: self._save(identity, token, state)
        finally:
            with db.connect() as conn:
                conn.execute("UPDATE skill_managed_resources SET lease_token=NULL,lease_until=0 "
                             "WHERE guild_id=? AND skill_id=? AND resource_key=? AND lease_token=?", (*identity, token))

    def _save(self, identity, token, state):
        state.pop("importBaselineHash", None)
        with db.connect() as conn:
            changed = conn.execute("UPDATE skill_managed_resources SET state_json=? WHERE guild_id=? AND skill_id=? "
                                   "AND resource_key=? AND lease_token=?", (json.dumps(state), *identity, token)).rowcount
            if not changed:
                raise TransientHostError("Managed resource lease expired; reconcile before retrying.")

    async def _members(self, ids):
        if len(ids) > 500 or any(type(value) is not int or value <= 0 for value in ids):
            raise InvalidHostOperationError("Invalid resource membership.")
        members = []
        for member_id in sorted(set(ids)):
            member = self.guild.get_member(member_id)
            if member is None:
                member = await self.guild.fetch_member(member_id)
            if member.guild.id != self.guild.id:
                raise InvalidHostOperationError("Resource members must belong to this guild.")
            members.append(member)
        return members

    async def _revocation_target(self, member_id):
        target = self.guild.get_member(int(member_id))
        if target is None and self.user_lookup is not None:
            # Discord permits member overwrites by User identity, including a
            # departed guild member. Clear access before a future rejoin.
            target = await self.user_lookup(int(member_id))
        if target is None:
            raise TransientHostError("Member revocation requires verified user identity; mapping retained.")
        return target

    async def _channel(self, state, kind, parent, name, members, visibility="private", chat=True, save=None):
        # Fetch the authoritative channel inventory before creating/recovering.
        channels = await self.guild.fetch_channels()
        owned = [c for c in channels if c.id == state.get("channelId")]
        if kind == "text":
            marked = [c for c in channels if isinstance(c, discord.TextChannel) and c.topic == state["marker"]]
            if len(marked) > 1:
                raise InvalidHostOperationError("Ambiguous managed resource ownership; manual reconciliation required.")
            if not owned:
                owned = marked
        if owned:
            channel = owned[0]
            expected = discord.TextChannel if kind == "text" else discord.VoiceChannel
            if not isinstance(channel, expected) or channel.guild.id != self.guild.id or channel.category_id != parent.id:
                raise InvalidHostOperationError("Stored resource ownership or parent changed; manual review required.")
            if kind == "text" and channel.topic != state["marker"]:
                raise InvalidHostOperationError("Stored resource ownership marker changed.")
            return channel
        if state.get("creating"):
            raise TransientHostError("Channel creation outcome is uncertain; Host reconciliation required.")
        if kind == "text" and self.skill_id == "event-manager" and state.get("channelId"):
            raise InvalidHostOperationError("The previously bound event channel is missing; Host review required.")
        overwrites = {self.guild.default_role: discord.PermissionOverwrite(view_channel=visibility == "public", send_messages=chat)}
        overwrites[self.guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        state["creating"] = True
        state["reserved"] = True
        save()
        return await (self.guild.create_text_channel(name, category=parent, topic=state["marker"], overwrites=overwrites,
                      reason="Host-managed Skill resource") if kind == "text" else
                      self.guild.create_voice_channel(name, category=parent, overwrites=overwrites, reason="Host-managed Skill voice"))

    async def ensure_area(self, *, key, name, visibility, member_ids, chat_enabled, overview):
        parent = self._policy()
        if visibility not in ("public", "private") or type(chat_enabled) is not bool:
            raise InvalidHostOperationError("Invalid managed area policy.")
        if visibility == "public" and not self.policy.get("allowPublic", False):
            raise InvalidHostOperationError("Host policy does not permit public Skill areas.")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or not isinstance(overview, str) or len(overview) > 4000:
            raise InvalidHostOperationError("Invalid managed area content.")
        if self.skill_id == "event-manager":
            name = re.sub(r"\s+", "-", name.strip().lower())[:100]
        members = await self._members(member_ids)
        with self._claim(key, "text") as (state, save):
            channel = await self._channel(state, "text", parent, name, members, visibility, chat_enabled, save)
            state["channelId"] = channel.id
            state.pop("creating", None)
            state["synchronizationPending"] = True
            save()
            overwrite = channel.overwrites_for(self.guild.default_role)
            overwrite.view_channel, overwrite.send_messages = visibility == "public", chat_enabled if visibility == "public" else False
            await channel.set_permissions(self.guild.default_role, overwrite=overwrite, reason="Host resource visibility policy")
            # Restore only fields previously managed by this adapter. Unrelated
            # role/member overwrites and every other permission bit are retained.
            requested = {str(member.id): member for member in members}
            for member_id, original in list(state["members"].items()):
                if member_id not in requested:
                    member = await self._revocation_target(member_id)
                    overwrite = channel.overwrites_for(member)
                    overwrite.view_channel, overwrite.send_messages = original
                    await channel.set_permissions(member, overwrite=overwrite, reason="Skill membership removed")
                    del state["members"][member_id]
            for member_id, member in requested.items():
                overwrite = channel.overwrites_for(member)
                state["members"].setdefault(member_id, [overwrite.view_channel, overwrite.send_messages])
                save()  # crash recovery retains the original values
                overwrite.view_channel, overwrite.send_messages = True, chat_enabled
                await channel.set_permissions(member, overwrite=overwrite, reason="Skill membership reconciled")
            await channel.edit(name=name, reason="Host-managed resource configuration")
            save()
            message = None
            if state.get("messageId"):
                try:
                    message = await channel.fetch_message(state["messageId"])
                    if message.author.id != self.guild.me.id:
                        raise InvalidHostOperationError("Overview message is not Host-owned.")
                except discord.NotFound:
                    state.pop("messageId", None)
                    state["messagePending"] = False
            if message is None and state.get("messagePending"):
                matches = [m async for m in channel.history(limit=100) if m.author.id == self.guild.me.id and
                           has_publication_marker(m, state["marker"])]
                if len(matches) != 1:
                    raise TransientHostError("Overview publication outcome is uncertain; manual reconciliation required.")
                message = matches[0]
            embed = discord.Embed(description=overview)
            view = event_card_controls(state["marker"]) if self.skill_id == "event-manager" else None
            if view is None:
                embed.set_footer(text=state["marker"])
            if message is None:
                state["messagePending"] = True
                save()
                message = await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            else:
                await message.edit(content=None, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            state["messageId"], state["messagePending"] = message.id, False
            state["synchronizationPending"] = False
            save()
            return {"channelId": str(channel.id), "messageId": str(message.id), "resourceKey": key}

    async def ensure_voice(self, *, key, name, member_ids):
        parent = self._policy(voice=True)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise InvalidHostOperationError("Invalid voice name.")
        members = await self._members(member_ids)
        with self._claim(key, "voice") as (state, save):
            channel = await self._channel(state, "voice", parent, name, members, save=save)
            state["channelId"] = channel.id
            state.pop("creating", None)
            state["synchronizationPending"] = True
            save()
            requested = {str(member.id): member for member in members}
            for member_id, original in list(state["members"].items()):
                if member_id not in requested:
                    member = await self._revocation_target(member_id)
                    overwrite = channel.overwrites_for(member)
                    overwrite.view_channel, overwrite.connect = original
                    await channel.set_permissions(member, overwrite=overwrite, reason="Voice participant removed")
                    del state["members"][member_id]
            for member_id, member in requested.items():
                overwrite = channel.overwrites_for(member)
                state["members"].setdefault(member_id, [overwrite.view_channel, overwrite.connect])
                save()
                overwrite.view_channel, overwrite.connect = True, True
                await channel.set_permissions(member, overwrite=overwrite, reason="Voice participants reconciled")
            state["synchronizationPending"] = False
            save()
            return {"channelId": str(channel.id), "resourceKey": key}

    async def retire_voice(self, *, key):
        parent = self._policy(voice=True)
        with self._claim(key, "voice", reserve=False) as (state, save):
            channel = next((channel for channel in await self.guild.fetch_channels() if channel.id == state.get("channelId")), None)
            if channel is None:
                state.pop("channelId", None)
                state.pop("retiring", None)
                state["reserved"] = False
                save()
                return True
            if not isinstance(channel, discord.VoiceChannel) or channel.guild.id != self.guild.id or channel.category_id != parent.id:
                raise InvalidHostOperationError("Voice resource ownership changed.")
            if state.get("retainLegacy"):
                # Existing customer channels need separately approved cleanup.
                return False
            if channel.members:
                return False
            state["retiring"] = True
            save()
            await channel.delete(reason="Owned empty temporary Skill voice session completed")
            state.pop("channelId", None)
            state.pop("retiring", None)
            state["reserved"] = False
            save()
            return True
