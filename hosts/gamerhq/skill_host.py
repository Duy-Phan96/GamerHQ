"""GamerHQ adapters for portable Skill Runtime contracts.

This module may import GamerHQ infrastructure. skill_runtime/ must never import
this module or any other GamerHQ host implementation.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Iterable, Mapping

from database import db
from skill_runtime.contracts.capabilities import KNOWN_CAPABILITIES, SkillCapability
from skill_runtime.contracts.errors import CapabilityUnavailableError
from skill_runtime.contracts.manifest import SKILL_ID

_STORAGE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_STORAGE_JSON_BYTES = 64 * 1024


def _valid_identity(guild_id: int, skill_id: str) -> None:
    if guild_id <= 0:
        raise ValueError("guild_id must be positive.")
    if not SKILL_ID.fullmatch(skill_id):
        raise ValueError("skill_id must be a valid stable Skill ID.")


class GamerHQSkillStateStore:
    """SQLite-backed per-guild installation + enablement state owned by the host."""

    async def is_installed(self, *, guild_id: int, skill_id: str) -> bool:
        _valid_identity(guild_id, skill_id)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT installed FROM skill_guild_state WHERE guild_id=? AND skill_id=?",
                (guild_id, skill_id),
            ).fetchone()
        return bool(row and row["installed"])

    async def install(self, *, guild_id: int, skill_id: str, version: str) -> bool:
        _valid_identity(guild_id, skill_id)
        if not version:
            raise ValueError("Skill version is required.")
        with db.connect() as conn:
            row = conn.execute(
                "SELECT installed FROM skill_guild_state WHERE guild_id=? AND skill_id=?",
                (guild_id, skill_id),
            ).fetchone()
            if row and row["installed"]:
                return False
            conn.execute(
                """
                INSERT INTO skill_guild_state(
                    guild_id,skill_id,installed,configured,enabled,version,updated_at
                )
                VALUES(?,?,1,0,0,?,?)
                ON CONFLICT(guild_id,skill_id) DO UPDATE SET
                    installed=1,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (guild_id, skill_id, version, int(time.time())),
            )
        return True

    async def installed_skill_ids(self, *, guild_id: int) -> tuple[str, ...]:
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT skill_id FROM skill_guild_state "
                "WHERE guild_id=? AND installed=1 ORDER BY skill_id",
                (guild_id,),
            ).fetchall()
        return tuple(row["skill_id"] for row in rows)

    async def is_configured(self, *, guild_id: int, skill_id: str) -> bool:
        _valid_identity(guild_id, skill_id)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT configured FROM skill_guild_state WHERE guild_id=? AND skill_id=?",
                (guild_id, skill_id),
            ).fetchone()
        return bool(row and row["configured"])

    async def set_configured(
        self,
        *,
        guild_id: int,
        skill_id: str,
        configured: bool,
        version: str,
    ) -> None:
        _valid_identity(guild_id, skill_id)
        if not version:
            raise ValueError("Skill version is required.")
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_guild_state(
                    guild_id,skill_id,installed,configured,enabled,version,updated_at
                )
                VALUES(?,?,1,?,0,?,?)
                ON CONFLICT(guild_id,skill_id) DO UPDATE SET
                    installed=1,
                    configured=excluded.configured,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (
                    guild_id,
                    skill_id,
                    int(bool(configured)),
                    version,
                    int(time.time()),
                ),
            )

    async def is_enabled(self, *, guild_id: int, skill_id: str) -> bool:
        _valid_identity(guild_id, skill_id)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM skill_guild_state WHERE guild_id=? AND skill_id=?",
                (guild_id, skill_id),
            ).fetchone()
        return bool(row and row["enabled"])

    async def set_enabled(self, *, guild_id: int, skill_id: str, enabled: bool, version: str) -> None:
        _valid_identity(guild_id, skill_id)
        if not version:
            raise ValueError("Skill version is required.")
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_guild_state(
                    guild_id,skill_id,installed,configured,enabled,version,updated_at
                )
                VALUES(?,?,1,0,?,?,?)
                ON CONFLICT(guild_id,skill_id) DO UPDATE SET
                    installed=1,
                    enabled=excluded.enabled,
                    version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (guild_id, skill_id, int(bool(enabled)), version, int(time.time())),
            )

    async def enabled_skill_ids(self, *, guild_id: int) -> tuple[str, ...]:
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT skill_id FROM skill_guild_state WHERE guild_id=? AND enabled=1 ORDER BY skill_id",
                (guild_id,),
            ).fetchall()
        return tuple(row["skill_id"] for row in rows)


class CapabilityPermissions:
    """Runtime capability gate built from a Skill's declared manifest permissions."""

    def __init__(self, declared: Iterable[str], *, available: Iterable[str] = KNOWN_CAPABILITIES):
        self._declared = frozenset(str(value) for value in declared)
        self._available = frozenset(str(value) for value in available)
        unknown = self._declared - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError("Unknown declared Skill capability: " + ", ".join(sorted(unknown)))

    @property
    def declared(self) -> frozenset[str]:
        return self._declared

    @property
    def available(self) -> frozenset[str]:
        return self._available

    def missing_declared(self) -> tuple[str, ...]:
        return tuple(sorted(self._declared - self._available))

    def require_all_declared(self) -> None:
        missing = self.missing_declared()
        if missing:
            raise CapabilityUnavailableError(
                "Host does not provide required Skill capabilities: " + ", ".join(missing) + "."
            )

    def allows(self, capability: str) -> bool:
        return capability in self._declared and capability in self._available

    def require(self, capability: str) -> None:
        if capability not in KNOWN_CAPABILITIES:
            raise PermissionError("Unknown Skill capability.")
        if capability not in self._declared:
            raise PermissionError(f"Skill did not declare capability: {capability}.")
        if capability not in self._available:
            raise CapabilityUnavailableError(f"Host does not provide capability: {capability}.")


class GamerHQSkillStorage:
    """Namespaced JSON storage visible to exactly one guild + Skill pair."""

    def __init__(self, *, guild_id: int, skill_id: str, permissions: CapabilityPermissions):
        _valid_identity(guild_id, skill_id)
        self.guild_id = guild_id
        self.skill_id = skill_id
        self.permissions = permissions

    def _key(self, key: str) -> str:
        if not _STORAGE_KEY.fullmatch(str(key)):
            raise ValueError("Skill storage key must be 1-128 safe characters.")
        return str(key)

    async def get(self, key: str) -> Any | None:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        key = self._key(key)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM skill_storage WHERE guild_id=? AND skill_id=? AND storage_key=?",
                (self.guild_id, self.skill_id, key),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value_json"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Stored Skill value is invalid JSON.") from exc

    async def set(self, key: str, value: Any) -> None:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        key = self._key(key)
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Skill storage values must be JSON serializable.") from exc
        if len(encoded.encode("utf-8")) > _MAX_STORAGE_JSON_BYTES:
            raise ValueError("Skill storage value exceeds the 64 KiB host limit.")
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_storage(guild_id,skill_id,storage_key,value_json,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id,skill_id,storage_key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (self.guild_id, self.skill_id, key, encoded, int(time.time())),
            )

    async def compare_and_set(self, key: str, expected: Any | None, value: Any) -> bool:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        self.permissions.require(SkillCapability.STORAGE_ATOMIC.value)
        key = self._key(key)
        if value is None:
            raise ValueError("Atomic storage does not accept JSON null.")
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
            if expected is not None:
                json.dumps(expected, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Atomic storage requires finite JSON values.") from exc
        if len(encoded.encode("utf-8")) > _MAX_STORAGE_JSON_BYTES:
            raise ValueError("Skill storage value exceeds the 64 KiB host limit.")
        with db.connect() as conn:
            # Lock before reading so independent processes cannot both win.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value_json FROM skill_storage WHERE guild_id=? AND skill_id=? AND storage_key=?",
                (self.guild_id, self.skill_id, key),
            ).fetchone()
            if (row is None) != (expected is None):
                return False
            if row is not None and json.dumps(json.loads(row["value_json"]), ensure_ascii=False,
                    separators=(",", ":"), sort_keys=True, allow_nan=False) != json.dumps(
                    expected, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False):
                return False
            conn.execute(
                "INSERT INTO skill_storage(guild_id,skill_id,storage_key,value_json,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(guild_id,skill_id,storage_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (self.guild_id, self.skill_id, key, encoded, int(time.time())),
            )
        return True

    async def list_keys(self, *, prefix: str, after: str = "", limit: int = 100) -> tuple[str, ...]:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        self.permissions.require(SkillCapability.STORAGE_ATOMIC.value)
        prefix = self._key(prefix)
        if after:
            after = self._key(after)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Storage page size must be between 1 and 1000.")
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT storage_key FROM skill_storage WHERE guild_id=? AND skill_id=? "
                "AND substr(storage_key,1,?)=? AND storage_key>? ORDER BY storage_key LIMIT ?",
                (self.guild_id, self.skill_id, len(prefix), prefix, after, limit),
            ).fetchall()
        return tuple(row["storage_key"] for row in rows)

    async def transact(self, *, checks, writes) -> bool:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        self.permissions.require(SkillCapability.STORAGE_ATOMIC.value)
        if not isinstance(checks, Mapping) or not isinstance(writes, Mapping) or len(set(checks) | set(writes)) > 10000:
            raise ValueError("Atomic storage requires bounded key mappings.")
        def encode(value):
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if len(encoded.encode()) > _MAX_STORAGE_JSON_BYTES:
                raise ValueError("Skill storage value exceeds the 64 KiB host limit.")
            return encoded
        expected = {self._key(key): encode(value) if value is not None else None for key, value in checks.items()}
        encoded = {self._key(key): encode(value) if value is not None else None for key, value in writes.items()}
        if sum(len(value or "") for value in (*expected.values(), *encoded.values())) > 16 * 1024 * 1024:
            raise ValueError("Atomic storage transaction exceeds 16 MiB.")
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for key, value in expected.items():
                row = conn.execute("SELECT value_json FROM skill_storage WHERE guild_id=? AND skill_id=? AND storage_key=?",
                                   (self.guild_id, self.skill_id, key)).fetchone()
                if (row is None) != (value is None) or (row is not None and encode(json.loads(row["value_json"])) != value):
                    return False
            for key, value in encoded.items():
                if value is None:
                    conn.execute("DELETE FROM skill_storage WHERE guild_id=? AND skill_id=? AND storage_key=?",
                                 (self.guild_id, self.skill_id, key))
                else:
                    conn.execute("INSERT INTO skill_storage VALUES(?,?,?,?,?) ON CONFLICT(guild_id,skill_id,storage_key) "
                                 "DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                                 (self.guild_id, self.skill_id, key, value, int(time.time())))
        return True

    async def delete(self, key: str) -> None:
        self.permissions.require(SkillCapability.STORAGE_SKILL.value)
        key = self._key(key)
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM skill_storage WHERE guild_id=? AND skill_id=? AND storage_key=?",
                (self.guild_id, self.skill_id, key),
            )


class GamerHQSkillAudit:
    """Adapter onto the existing private GamerHQ server log; no competing audit UI."""

    def __init__(self, *, guild, skill_id: str, permissions: CapabilityPermissions):
        _valid_identity(guild.id, skill_id)
        self.guild = guild
        self.skill_id = skill_id
        self.permissions = permissions
        self.log = logging.getLogger(f"gamerhq.skill.{skill_id}")

    async def write(
        self,
        *,
        action: str,
        target: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.permissions.require(SkillCapability.AUDIT_WRITE.value)
        if not action or len(action) > 100:
            raise ValueError("Skill audit action must be 1-100 characters.")
        # Metadata values may contain private Skill data. The shared operational
        # log gets only bounded keys; richer structured Skill audit can be added
        # later by extending the existing audit infrastructure, not by leaking it.
        keys = ", ".join(sorted(str(key)[:40] for key in (metadata or {})))[:300]
        detail = f"Skill **{self.skill_id}** · action **{action}**"
        if target:
            detail += f"\nTarget: {str(target)[:160]}"
        if keys:
            detail += f"\nMetadata fields: {keys}"
        from services.server_log_service import emit
        sent = await emit(
            self.guild,
            "skill:" + uuid.uuid4().hex,
            "🧩 Skill Activity",
            detail,
        )
        if not sent:
            self.log.info("skill_audit action=%s result=server_log_unavailable", action)
