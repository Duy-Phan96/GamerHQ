"""Restricted external HTTP adapter for portable Skills."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.context import ExternalHttpResponse
from skill_runtime.contracts.errors import InvalidHostOperationError, TransientHostError

from .skill_host import CapabilityPermissions

_MAX_REQUEST_JSON_BYTES = 256 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


async def _validate_public_https_url(url: str) -> str:
    value = str(url).strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise InvalidHostOperationError("External Skill HTTP requests must use HTTPS.")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise InvalidHostOperationError("External Skill HTTP URL is invalid.")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".local"):
        raise InvalidHostOperationError("External Skill HTTP URL must use a public host.")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        port = parsed.port or 443
        try:
            resolved = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise TransientHostError("External Skill HTTP host could not be resolved.") from exc
        addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}
        if not addresses or any(not address.is_global for address in addresses):
            raise InvalidHostOperationError("External Skill HTTP URL must resolve only to public addresses.")
    else:
        if not literal.is_global:
            raise InvalidHostOperationError("External Skill HTTP URL must use a public address.")
    return value


class GamerHQExternalHttp:
    """Capability-gated HTTPS client with bounded requests and responses."""

    def __init__(self, *, permissions: CapabilityPermissions):
        self.permissions = permissions

    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, str | int | float | bool] | None = None,
        json_body: Any | None = None,
        timeout_seconds: float = 15.0,
    ) -> ExternalHttpResponse:
        self.permissions.require(SkillCapability.HTTP_EXTERNAL.value)
        normalized_method = str(method).upper().strip()
        if normalized_method not in _ALLOWED_METHODS:
            raise InvalidHostOperationError("Unsupported external Skill HTTP method.")
        target = await _validate_public_https_url(url)
        timeout = float(timeout_seconds)
        if not 0 < timeout <= 30:
            raise InvalidHostOperationError("External Skill HTTP timeout must be between 0 and 30 seconds.")

        safe_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        if any("\r" in key or "\n" in key or "\r" in value or "\n" in value for key, value in safe_headers.items()):
            raise InvalidHostOperationError("External Skill HTTP headers contain invalid newline characters.")

        if json_body is not None:
            import json
            encoded = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > _MAX_REQUEST_JSON_BYTES:
                raise InvalidHostOperationError("External Skill HTTP JSON body exceeds the 256 KiB host limit.")

        client_timeout = aiohttp.ClientTimeout(total=timeout)
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.request(
                    normalized_method,
                    target,
                    headers=safe_headers or None,
                    params=dict(query or {}) or None,
                    json=json_body,
                    allow_redirects=False,
                ) as response:
                    body = await response.content.read(_MAX_RESPONSE_BYTES + 1)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise InvalidHostOperationError(
                            "External Skill HTTP response exceeds the 2 MiB host limit."
                        )
                    try:
                        text = body.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise InvalidHostOperationError(
                            "External Skill HTTP response must be UTF-8 text."
                        ) from exc
                    return ExternalHttpResponse(
                        status=response.status,
                        headers={str(key): str(value) for key, value in response.headers.items()},
                        body=text,
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TransientHostError("External Skill HTTP request failed.") from exc
