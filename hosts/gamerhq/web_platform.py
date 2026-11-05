"""Transport-neutral application service for the GamerHQ web client.

HTTP/OAuth transport belongs outside the Discord bot core. This service exposes
JSON-safe projections over the authoritative GamerHQ Skill Runtime and requires
the caller to provide guild IDs that were already authorized server-side.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping
import discord

from skill_runtime.contracts.context import SkillContext


class WebPlatformAuthorizationError(PermissionError):
    pass


MEMBER_EVENT_CONTRACTS = frozenset("event-manager." + name + ".v1" for name in
    ("my-events", "attendance-plan", "attendance-save", "invitations", "invite-response"))


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


class GamerHQWebPlatformService:
    """Authoritative web-facing projection over GamerHQ host/runtime state."""

    def __init__(self, *, bot, skill_runtime):
        self.bot = bot
        self.skill_runtime = skill_runtime

    @staticmethod
    def _authorized_set(authorized_guild_ids: Iterable[int]) -> set[int]:
        return {int(value) for value in authorized_guild_ids}

    def _require_guild(self, guild_id: int, authorized_guild_ids: Iterable[int]):
        guild_id = int(guild_id)
        if guild_id not in self._authorized_set(authorized_guild_ids):
            raise WebPlatformAuthorizationError("Guild is not authorized for this web session.")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise LookupError("Guild is not available to the GamerHQ host.")
        return guild

    async def _require_member(self, guild, actor_id):
        if actor_id is None:
            raise WebPlatformAuthorizationError("A signed-in Discord member is required.")
        member = guild.get_member(actor_id)
        if member is None:
            try:
                member = await guild.fetch_member(actor_id)
            except (discord.Forbidden, discord.NotFound) as error:
                raise WebPlatformAuthorizationError("Guild membership is required.") from error
        if member is None or member.guild.id != guild.id:
            raise WebPlatformAuthorizationError("Guild membership is required.")
        return member

    async def _require_manager_actor(self, guild, actor_id):
        # Existing trusted service operations carry no Discord actor. Browser
        # requests always carry the actor from the sealed Web session.
        if actor_id is not None:
            member = await self._require_member(guild, actor_id)
            permissions = member.guild_permissions
            if not (permissions.administrator or permissions.manage_guild):
                raise WebPlatformAuthorizationError("Discord Manage Server is required.")

    async def list_servers(self, *, authorized_guild_ids: Iterable[int]) -> list[dict[str, Any]]:
        allowed = self._authorized_set(authorized_guild_ids)
        result = []
        for guild in tuple(getattr(self.bot, "guilds", ())):
            if int(guild.id) not in allowed:
                continue
            result.append({
                "id": str(guild.id),
                "name": str(guild.name),
                "memberCount": int(getattr(guild, "member_count", 0) or 0),
                "manageable": True,
            })
        return sorted(result, key=lambda item: item["name"].lower())

    async def list_discord_channels(
        self,
        *,
        guild_id: int,
        authorized_guild_ids: Iterable[int],
    ) -> list[dict[str, Any]]:
        guild = self._require_guild(guild_id, authorized_guild_ids)
        result = []
        for channel in tuple(getattr(guild, "channels", ())):
            category = getattr(channel, "category", None)
            result.append({
                "id": str(channel.id),
                "name": str(channel.name),
                "kind": str(getattr(channel, "type", "unknown")),
                "position": int(getattr(channel, "position", 0) or 0),
                "categoryId": str(category.id) if category is not None else None,
                "categoryName": str(category.name) if category is not None else None,
            })
        return sorted(
            result,
            key=lambda item: (
                item["categoryName"] or "",
                item["position"],
                item["name"].lower(),
            ),
        )

    async def list_discord_roles(
        self,
        *,
        guild_id: int,
        authorized_guild_ids: Iterable[int],
    ) -> list[dict[str, Any]]:
        guild = self._require_guild(guild_id, authorized_guild_ids)
        result = []
        default_role = getattr(guild, "default_role", None)
        for role in tuple(getattr(guild, "roles", ())):
            result.append({
                "id": str(role.id),
                "name": str(role.name),
                "position": int(getattr(role, "position", 0) or 0),
                "managed": bool(getattr(role, "managed", False)),
                "isDefault": bool(default_role is not None and role.id == default_role.id),
            })
        return sorted(
            result,
            key=lambda item: (-item["position"], item["name"].lower()),
        )

    async def list_skills(
        self,
        *,
        guild_id: int,
        authorized_guild_ids: Iterable[int],
    ) -> list[dict[str, Any]]:
        self._require_guild(guild_id, authorized_guild_ids)
        statuses = await self.skill_runtime.statuses(guild_id=int(guild_id))
        return [self._skill_status(status) for status in statuses]

    async def get_skill(
        self,
        *,
        guild_id: int,
        skill_id: str,
        authorized_guild_ids: Iterable[int],
    ) -> dict[str, Any]:
        self._require_guild(guild_id, authorized_guild_ids)
        status = await self.skill_runtime.status(guild_id=int(guild_id), skill_id=skill_id)
        return self._skill_status(status)

    async def list_skill_deployments(self, *, guild_id: int, skill_id: str, authorized_guild_ids):
        self._require_guild(guild_id, authorized_guild_ids)
        status = await self.skill_runtime.status(guild_id=int(guild_id), skill_id=skill_id)
        if not status.installed:
            return []
        from .skill_update_controller import deployment_history
        # This is shared runtime package history. Internal deployment IDs and
        # diagnostics are reserved for Host operators.
        return [{"id": sha256(f"{guild_id}:{row['id']}".encode()).hexdigest(), "skill_id": skill_id,
                 "version": row["version"], "state": row["state"], "detail": "Host package applied.",
                 "created_at": row["created_at"], "updated_at": row["updated_at"]}
                for row in deployment_history(skill_id=skill_id) if row["state"] == "APPLIED"]

    async def get_management_schema(
        self,
        *,
        guild_id: int,
        skill_id: str,
        authorized_guild_ids: Iterable[int],
    ) -> dict[str, Any] | None:
        self._require_guild(guild_id, authorized_guild_ids)
        schema = self.skill_runtime.management_ui_schema(skill_id)
        if schema is None:
            return None
        raw = _json_value(schema)
        # Public JSON uses camelCase while Python contracts remain idiomatic.
        return {
            "version": raw["version"],
            "readContract": raw["read_contract"],
            "writeContract": raw["write_contract"],
            "document": (
                {
                    "readPath": raw["document"]["read_path"],
                    "writePath": raw["document"]["write_path"],
                    "revisionPath": raw["document"]["revision_path"],
                    "expectedRevisionKey": raw["document"]["expected_revision_key"],
                }
                if raw["document"] is not None
                else None
            ),
            "sections": [
                {
                    "id": section["id"],
                    "title": section["title"],
                    "description": section["description"],
                    "fields": [
                        {
                            "key": field["key"],
                            "label": field["label"],
                            "type": field["type"],
                            "configPath": field["config_path"],
                            "description": field["description"],
                            "required": field["required"],
                            "minimum": field["minimum"],
                            "maximum": field["maximum"],
                            "options": field["options"],
                            "collection": (
                                {
                                    "operations": {
                                        "listContract": field["collection"]["operations"]["list_contract"],
                                        "createContract": field["collection"]["operations"]["create_contract"],
                                        "getContract": field["collection"]["operations"]["get_contract"],
                                        "describeContract": field["collection"]["operations"]["describe_contract"],
                                        "validateContract": field["collection"]["operations"]["validate_contract"],
                                        "updateContract": field["collection"]["operations"]["update_contract"],
                                        "setActiveContract": field["collection"]["operations"]["set_active_contract"],
                                        "deletePreviewContract": field["collection"]["operations"]["delete_preview_contract"],
                                        "deleteContract": field["collection"]["operations"]["delete_contract"],
                                    },
                                    "itemFields": [
                                        {
                                            "key": item_field["key"],
                                            "label": item_field["label"],
                                            "type": item_field["type"],
                                            "configPath": item_field["config_path"],
                                            "description": item_field["description"],
                                            "required": item_field["required"],
                                            "minimum": item_field["minimum"],
                                            "maximum": item_field["maximum"],
                                            "options": item_field["options"],
                                        }
                                        for item_field in field["collection"]["item_fields"]
                                    ],
                                    "itemIdPath": field["collection"]["item_id_path"],
                                    "itemIdPayloadKey": field["collection"]["item_id_payload_key"],
                                    "itemReadPath": field["collection"]["item_read_path"],
                                    "scheduleHintsPath": field["collection"]["schedule_hints_path"],
                                    "revisionPath": field["collection"]["revision_path"],
                                    "expectedRevisionKey": field["collection"]["expected_revision_key"],
                                    "previewRequired": field["collection"]["preview_required"],
                                    "createRequestIdKey": field["collection"]["create_request_id_key"],
                                    "titlePath": field["collection"]["title_path"],
                                    "statusPath": field["collection"]["status_path"],
                                    "summaryPath": field["collection"]["summary_path"],
                                    "maxItems": field["collection"]["max_items"],
                                }
                                if field["collection"] is not None
                                else None
                            ),
                        }
                        for field in section["fields"]
                    ],
                }
                for section in raw["sections"]
            ],
        }

    async def call_management(
        self,
        *,
        guild_id: int,
        skill_id: str,
        contract_id: str,
        payload: Mapping[str, Any],
        authorized_guild_ids: Iterable[int],
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        guild = self._require_guild(guild_id, authorized_guild_ids)
        if skill_id == "event-manager" and contract_id in MEMBER_EVENT_CONTRACTS:
            await self._require_member(guild, actor_id)
            if contract_id == "event-manager.attendance-plan.v1":
                payload = {**payload, "manager": False}
        else:
            await self._require_manager_actor(guild, actor_id)
        if skill_id == "event-manager" and payload.get("convertLegacySchedule") is True:
            member = guild.get_member(actor_id) if actor_id is not None else None
            if member is None or not member.guild_permissions.administrator:
                raise WebPlatformAuthorizationError("Only an administrator can replace an imported event schedule.")
        result = await self.skill_runtime.call_management(
            guild_id=int(guild_id),
            skill_id=skill_id,
            contract_id=contract_id,
            payload=dict(payload),
            **({"actor_id": actor_id} if actor_id is not None else {}),
        )
        value = _json_value(result)
        if skill_id == "event-manager" and contract_id == "event-manager.attendance-plan.v1":
            for session in value.get("sessions", ()):
                members = [guild.get_member(int(user)) for user in session["participants"]]
                session["participantNames"] = [member.display_name if member else "Server member" for member in members]
                session["registered"] = len(session.pop("participants"))
        return value

    async def install_skill(
        self,
        *,
        guild_id: int,
        skill_id: str,
        authorized_guild_ids: Iterable[int],
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        guild = self._require_guild(guild_id, authorized_guild_ids)
        await self._require_manager_actor(guild, actor_id)
        status = await self.skill_runtime.status(guild_id=int(guild_id), skill_id=skill_id)
        if status.version == "unknown":
            raise LookupError("Skill package is unavailable to this Host.")
        await self.skill_runtime.install_skill(
            guild_id=int(guild_id),
            skill_id=skill_id,
        )
        return await self.get_skill(
            guild_id=int(guild_id),
            skill_id=skill_id,
            authorized_guild_ids=authorized_guild_ids,
        )

    async def set_skill_enabled(
        self,
        *,
        guild_id: int,
        skill_id: str,
        enabled: bool,
        authorized_guild_ids: Iterable[int],
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        guild = self._require_guild(guild_id, authorized_guild_ids)
        await self._require_manager_actor(guild, actor_id)
        if enabled:
            if actor_id is not None:
                status = await self.skill_runtime.status(guild_id=int(guild_id), skill_id=skill_id)
                if not status.installed or (not status.enabled and not status.configured):
                    raise ValueError("Install and configure this Skill before enabling it.")
                if not status.enabled and status.missing_capabilities:
                    raise ValueError("The Host must provide the missing capabilities before enabling this Skill.")
            await self.skill_runtime.enable_skill(guild_id=int(guild_id), skill_id=skill_id)
        else:
            await self.skill_runtime.disable_skill(guild_id=int(guild_id), skill_id=skill_id)
        return await self.get_skill(
            guild_id=int(guild_id),
            skill_id=skill_id,
            authorized_guild_ids=authorized_guild_ids,
        )

    @staticmethod
    def _skill_status(status) -> dict[str, Any]:
        return {
            "id": status.skill_id,
            "name": status.name,
            "version": status.version,
            "description": status.description,
            "source": {
                "kind": status.source_kind,
                "distribution": status.source_distribution,
            },
            "state": {
                "available": status.version != "unknown",
                "installed": bool(status.installed),
                "enabled": bool(status.enabled),
                "configured": bool(status.configured),
                "healthy": status.health == "PASS",
            },
            "health": {
                "state": status.health,
                "detail": status.health_detail,
            },
            "capabilities": list(status.required_capabilities),
            "missingCapabilities": list(status.missing_capabilities),
            "managementAvailable": bool(status.management_available),
            "managementSchemaAvailable": bool(status.management_schema_available),
        }
