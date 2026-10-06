import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from database import db
from hosts.gamerhq.skill_host import CapabilityPermissions
from hosts.gamerhq.skill_http import GamerHQExternalHttp, _validate_public_https_url
from hosts.gamerhq.skill_secrets import GamerHQSkillSecrets
from skill_runtime.contracts.capabilities import SkillCapability
from skill_runtime.contracts.context import ExternalHttpResponse
from skill_runtime.contracts.errors import CapabilityUnavailableError


class SkillHttpAndSecretsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "skills.db")
        self.db_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def permissions(self, *capabilities):
        return CapabilityPermissions(capabilities, available=capabilities)

    async def test_secret_store_encrypts_and_isolates_values(self):
        cap = SkillCapability.SKILL_SECURE_STORAGE.value
        key = Fernet.generate_key().decode()
        one = GamerHQSkillSecrets(
            guild_id=1,
            skill_id="awin-affiliate",
            permissions=self.permissions(cap),
            encryption_key=key,
        )
        other = GamerHQSkillSecrets(
            guild_id=2,
            skill_id="awin-affiliate",
            permissions=self.permissions(cap),
            encryption_key=key,
        )

        await one.set("access-token", "super-secret-token")
        self.assertEqual(await one.get("access-token"), "super-secret-token")
        self.assertIsNone(await other.get("access-token"))

        with db.connect() as conn:
            row = conn.execute(
                "SELECT value_encrypted FROM skill_secrets "
                "WHERE guild_id=1 AND skill_id='awin-affiliate' AND secret_key='access-token'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotIn("super-secret-token", row["value_encrypted"])

        await one.delete("access-token")
        self.assertIsNone(await one.get("access-token"))

    async def test_secret_store_requires_declared_capability(self):
        store = GamerHQSkillSecrets(
            guild_id=1,
            skill_id="awin-affiliate",
            permissions=self.permissions(),
            encryption_key=Fernet.generate_key().decode(),
        )
        with self.assertRaisesRegex(PermissionError, "did not declare"):
            await store.get("access-token")

    async def test_secret_store_fails_closed_without_host_encryption_key(self):
        cap = SkillCapability.SKILL_SECURE_STORAGE.value
        store = GamerHQSkillSecrets(
            guild_id=1,
            skill_id="awin-affiliate",
            permissions=self.permissions(cap),
            encryption_key="",
        )
        with self.assertRaises(CapabilityUnavailableError):
            await store.set("access-token", "secret")

    async def test_external_http_requires_capability_before_network_access(self):
        http = GamerHQExternalHttp(permissions=self.permissions())
        with self.assertRaisesRegex(PermissionError, "did not declare"):
            await http.request(method="GET", url="https://example.com")

    async def test_external_http_blocks_non_https_and_local_targets(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            await _validate_public_https_url("http://example.com")
        with self.assertRaisesRegex(ValueError, "public host"):
            await _validate_public_https_url("https://localhost/test")
        with self.assertRaisesRegex(ValueError, "public address"):
            await _validate_public_https_url("https://127.0.0.1/test")

    def test_external_http_response_has_json_helper(self):
        response = ExternalHttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            body='{"ok":true}',
        )
        self.assertEqual(response.json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
