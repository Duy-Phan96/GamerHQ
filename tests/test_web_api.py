import unittest

from aiohttp.test_utils import TestClient, TestServer

from hosts.gamerhq.web_api import create_web_api_app


class FakePlatformService:
    async def list_servers(self, *, authorized_guild_ids):
        return [{"id": str(value), "name": f"Guild {value}", "memberCount": 1, "manageable": True}
                for value in authorized_guild_ids]

    async def list_skills(self, *, guild_id, authorized_guild_ids):
        if guild_id not in set(authorized_guild_ids):
            from hosts.gamerhq.web_platform import WebPlatformAuthorizationError
            raise WebPlatformAuthorizationError()
        return [{"id": "demo", "name": "Demo"}]

    async def get_skill(self, **kwargs):
        return {"id": kwargs["skill_id"], "state": {"enabled": True}}

    async def get_management_schema(self, **kwargs):
        return {"version": "1", "readContract": "demo.read.v1", "writeContract": "demo.write.v1", "sections": []}

    async def call_management(self, **kwargs):
        return {"received": dict(kwargs["payload"])}

    async def set_skill_enabled(self, **kwargs):
        return {"id": kwargs["skill_id"], "state": {"enabled": kwargs["enabled"]}}


class WebApiTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = create_web_api_app(
            platform_service=FakePlatformService(),
            shared_secret="test-secret",
        )
        self.server = TestServer(app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    def headers(self, guilds="123"):
        return {
            "Authorization": "Bearer test-secret",
            "X-GamerHQ-Authorized-Guild-Ids": guilds,
        }

    async def test_service_auth_is_required(self):
        response = await self.client.get("/api/v1/me/servers")
        self.assertEqual(response.status, 401)
        payload = await response.json()
        self.assertEqual(payload["code"], "service_unauthorized")

    async def test_list_servers_uses_trusted_authorized_guild_header(self):
        response = await self.client.get(
            "/api/v1/me/servers",
            headers=self.headers("123,456"),
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual([item["id"] for item in payload["servers"]], ["123", "456"])

    async def test_invalid_authorized_guild_header_is_rejected(self):
        response = await self.client.get(
            "/api/v1/me/servers",
            headers=self.headers("123,nope"),
        )
        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertEqual(payload["code"], "invalid_request")

    async def test_guild_authorization_error_maps_to_403(self):
        response = await self.client.get(
            "/api/v1/servers/999/skills",
            headers=self.headers("123"),
        )
        self.assertEqual(response.status, 403)
        payload = await response.json()
        self.assertEqual(payload["code"], "guild_forbidden")

    async def test_management_payload_shape_is_preserved(self):
        response = await self.client.post(
            "/api/v1/servers/123/skills/demo/management/demo.write.v1",
            headers=self.headers(),
            json={"payload": {"expectedRevision": 7, "values": {"enabled": True}}},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["received"]["expectedRevision"], 7)

    async def test_enablement_requires_boolean(self):
        response = await self.client.put(
            "/api/v1/servers/123/skills/demo/enabled",
            headers=self.headers(),
            json={"enabled": "yes"},
        )
        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertEqual(payload["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
