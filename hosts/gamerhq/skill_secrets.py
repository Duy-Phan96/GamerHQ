"""Encrypted GamerHQ adapter for the portable Skill secret-store contract."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
import re
import time

from database import db
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.errors import CapabilityUnavailableError
from skill_runtime.contracts.manifest import SKILL_ID

from .skill_host import CapabilityPermissions

_SECRET_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_SECRET_BYTES = 16 * 1024


class GamerHQSkillSecrets:
    """Encrypted, namespaced secret storage for one guild + Skill pair."""

    def __init__(
        self,
        *,
        guild_id: int,
        skill_id: str,
        permissions: CapabilityPermissions,
        encryption_key: str,
    ):
        if guild_id <= 0:
            raise ValueError("guild_id must be positive.")
        if not SKILL_ID.fullmatch(skill_id):
            raise ValueError("skill_id must be a valid stable Skill ID.")
        self._fernet = None
        if encryption_key:
            try:
                self._fernet = Fernet(encryption_key.encode("ascii"))
            except (UnicodeEncodeError, ValueError) as exc:
                raise ValueError("Skill secret encryption key is invalid.") from exc
        self.guild_id = guild_id
        self.skill_id = skill_id
        self.permissions = permissions

    def _key(self, key: str) -> str:
        value = str(key)
        if not _SECRET_KEY.fullmatch(value):
            raise ValueError("Skill secret key must be 1-128 safe characters.")
        return value

    async def get(self, key: str) -> str | None:
        self.permissions.require(SkillCapability.SKILL_SECURE_STORAGE.value)
        if self._fernet is None:
            raise CapabilityUnavailableError("Skill secret storage is not configured.")
        key = self._key(key)
        with db.connect() as conn:
            row = conn.execute(
                "SELECT value_encrypted FROM skill_secrets "
                "WHERE guild_id=? AND skill_id=? AND secret_key=?",
                (self.guild_id, self.skill_id, key),
            ).fetchone()
        if row is None:
            return None
        try:
            decrypted = self._fernet.decrypt(str(row["value_encrypted"]).encode("ascii"))
            return decrypted.decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise ValueError("Stored Skill secret cannot be decrypted.") from exc

    async def set(self, key: str, value: str) -> None:
        self.permissions.require(SkillCapability.SKILL_SECURE_STORAGE.value)
        if self._fernet is None:
            raise CapabilityUnavailableError("Skill secret storage is not configured.")
        key = self._key(key)
        if not isinstance(value, str) or not value:
            raise ValueError("Skill secret value must be a non-empty string.")
        encoded = value.encode("utf-8")
        if len(encoded) > _MAX_SECRET_BYTES:
            raise ValueError("Skill secret exceeds the 16 KiB host limit.")
        encrypted = self._fernet.encrypt(encoded).decode("ascii")
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_secrets(guild_id,skill_id,secret_key,value_encrypted,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id,skill_id,secret_key) DO UPDATE SET
                    value_encrypted=excluded.value_encrypted,
                    updated_at=excluded.updated_at
                """,
                (self.guild_id, self.skill_id, key, encrypted, int(time.time())),
            )

    async def delete(self, key: str) -> None:
        self.permissions.require(SkillCapability.SKILL_SECURE_STORAGE.value)
        if self._fernet is None:
            raise CapabilityUnavailableError("Skill secret storage is not configured.")
        key = self._key(key)
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM skill_secrets WHERE guild_id=? AND skill_id=? AND secret_key=?",
                (self.guild_id, self.skill_id, key),
            )
