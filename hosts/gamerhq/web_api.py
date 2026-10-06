"""Thin authenticated HTTP transport for the GamerHQ web platform service.

This module owns transport concerns only. Authoritative server/Skill state stays
inside GamerHQWebPlatformService and the existing Skill Runtime.
"""
from __future__ import annotations

import hmac
import json
from collections.abc import Iterable, Mapping
from typing import Any

from aiohttp import web

from hosts.gamerhq.web_platform import (
    GamerHQWebPlatformService,
    WebPlatformAuthorizationError,
)
from skill_runtime.runtime.management_router import (
    SkillManagementConflictError,
    SkillManagementError,
)

_AUTHORIZED_GUILDS_HEADER = "X-GamerHQ-Authorized-Guild-Ids"


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"code": code, "message": message},
        status=status,
    )


def _parse_authorized_guild_ids(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    values: list[int] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        if not candidate.isdigit() or int(candidate) <= 0:
            raise ValueError("Authorized guild IDs must be positive integers.")
        values.append(int(candidate))
    return tuple(dict.fromkeys(values))


async def _json_object(request: web.Request) -> Mapping[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest, web.HTTPUnsupportedMediaType):
        raise ValueError("Request body must be valid JSON.") from None
    if not isinstance(payload, Mapping):
        raise ValueError("Request body must be a JSON object.")
    return payload


@web.middleware
async def _service_auth(request: web.Request, handler):
    expected = request.app["shared_secret"]
    supplied = request.headers.get("Authorization", "")
    prefix = "Bearer "
    token = supplied[len(prefix):] if supplied.startswith(prefix) else ""
    if not token or not hmac.compare_digest(token, expected):
        return _error(401, "service_unauthorized", "Trusted GamerHQ web service authentication failed.")
    return await handler(request)


@web.middleware
async def _safe_errors(request: web.Request, handler):
    try:
        return await handler(request)
    except WebPlatformAuthorizationError:
        return _error(403, "guild_forbidden", "This server is not authorized for the current web session.")
    except (LookupError, KeyError):
        return _error(404, "not_found", "The requested GamerHQ resource was not found.")
    except SkillManagementConflictError as exc:
        return _error(409, "management_conflict", str(exc))
    except SkillManagementError as exc:
        return _error(400, "management_rejected", str(exc))
    except ValueError as exc:
        return _error(400, "invalid_request", str(exc))
    except web.HTTPException:
        raise
    except Exception:
        return _error(500, "unexpected_error", "GamerHQ could not complete the request.")


def _authorized_guild_ids(request: web.Request) -> tuple[int, ...]:
    return _parse_authorized_guild_ids(request.headers.get(_AUTHORIZED_GUILDS_HEADER))


def _service(request: web.Request) -> GamerHQWebPlatformService:
    return request.app["platform_service"]


async def list_servers(request: web.Request) -> web.Response:
    result = await _service(request).list_servers(
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response({"servers": result})


async def list_discord_channels(request: web.Request) -> web.Response:
    result = await _service(request).list_discord_channels(
        guild_id=int(request.match_info["guild_id"]),
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response({"channels": result})


async def list_discord_roles(request: web.Request) -> web.Response:
    result = await _service(request).list_discord_roles(
        guild_id=int(request.match_info["guild_id"]),
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response({"roles": result})


async def list_skills(request: web.Request) -> web.Response:
    result = await _service(request).list_skills(
        guild_id=int(request.match_info["guild_id"]),
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response({"skills": result})


async def get_skill(request: web.Request) -> web.Response:
    result = await _service(request).get_skill(
        guild_id=int(request.match_info["guild_id"]),
        skill_id=request.match_info["skill_id"],
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response(result)


async def get_management_schema(request: web.Request) -> web.Response:
    result = await _service(request).get_management_schema(
        guild_id=int(request.match_info["guild_id"]),
        skill_id=request.match_info["skill_id"],
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response({"schema": result})


async def call_management(request: web.Request) -> web.Response:
    body = await _json_object(request)
    payload = body.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("Management request requires a JSON object payload.")
    result = await _service(request).call_management(
        guild_id=int(request.match_info["guild_id"]),
        skill_id=request.match_info["skill_id"],
        contract_id=request.match_info["contract_id"],
        payload=payload,
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response(result)


async def install_skill(request: web.Request) -> web.Response:
    result = await _service(request).install_skill(
        guild_id=int(request.match_info["guild_id"]),
        skill_id=request.match_info["skill_id"],
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response(result)


async def set_skill_enabled(request: web.Request) -> web.Response:
    body = await _json_object(request)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean.")
    result = await _service(request).set_skill_enabled(
        guild_id=int(request.match_info["guild_id"]),
        skill_id=request.match_info["skill_id"],
        enabled=enabled,
        authorized_guild_ids=_authorized_guild_ids(request),
    )
    return web.json_response(result)


def create_web_api_app(
    *,
    platform_service: GamerHQWebPlatformService,
    shared_secret: str,
) -> web.Application:
    if not shared_secret:
        raise ValueError("shared_secret is required.")
    app = web.Application(middlewares=[_safe_errors, _service_auth])
    app["platform_service"] = platform_service
    app["shared_secret"] = shared_secret
    app.add_routes(
        [
            web.get("/api/v1/me/servers", list_servers),
            web.get(
                "/api/v1/servers/{guild_id}/resources/channels",
                list_discord_channels,
            ),
            web.get(
                "/api/v1/servers/{guild_id}/resources/roles",
                list_discord_roles,
            ),
            web.get("/api/v1/servers/{guild_id}/skills", list_skills),
            web.get("/api/v1/servers/{guild_id}/skills/{skill_id}", get_skill),
            web.get(
                "/api/v1/servers/{guild_id}/skills/{skill_id}/management-schema",
                get_management_schema,
            ),
            web.post(
                "/api/v1/servers/{guild_id}/skills/{skill_id}/management/{contract_id}",
                call_management,
            ),
            web.post(
                "/api/v1/servers/{guild_id}/skills/{skill_id}/install",
                install_skill,
            ),
            web.put(
                "/api/v1/servers/{guild_id}/skills/{skill_id}/enabled",
                set_skill_enabled,
            ),
        ]
    )
    return app


class GamerHQWebApiServer:
    """Lifecycle wrapper for the optional internal aiohttp listener."""

    def __init__(
        self,
        *,
        bot,
        skill_runtime,
        shared_secret: str,
        host: str = "127.0.0.1",
        port: int = 8080,
    ):
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None
        service = GamerHQWebPlatformService(bot=bot, skill_runtime=skill_runtime)
        self.app = create_web_api_app(
            platform_service=service,
            shared_secret=shared_secret,
        )

    async def start(self) -> None:
        if self._runner is not None:
            return
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, host=self.host, port=self.port)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner

    async def close(self) -> None:
        runner = self._runner
        self._runner = None
        if runner is not None:
            await runner.cleanup()
