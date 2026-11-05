"""Disposable Web/Host integration fixture. Never connects to Discord."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from aiohttp import web

from database import db
from hosts.gamerhq.skill_runtime import GamerHQSkillRuntime
from hosts.gamerhq.web_api import create_web_api_app
from hosts.gamerhq.web_platform import GamerHQWebPlatformService
from skill_runtime import (
    ManagementApiContract, ManagementConflictError, ManagementDocumentBinding, ManagementField,
    ManagementSection, ManagementUiSchema, SkillHealth, SkillManagementApis, SkillManifest,
)


class FixtureSkill:
    read = "web-fixture.read.v1"
    write = "web-fixture.write.v1"
    manifest = SkillManifest(
        id="web-fixture", name="Web Fixture", version="1.0.0", runtime_api_version="1",
        description="Disposable lifecycle test.", author="GamerHQ CI",
        permissions=("storage.skill", "storage.atomic"),
        management_apis=SkillManagementApis(exposes=(ManagementApiContract(read), ManagementApiContract(write))),
        management_ui=ManagementUiSchema(version="1", read_contract=read, write_contract=write,
            document=ManagementDocumentBinding(read_path="config", write_path="config",
                revision_path="revision", expected_revision_key="expectedRevision"),
            sections=(ManagementSection(id="settings", title="Settings",
                fields=(ManagementField(key="label", label="Label", type="string", config_path="label"),)),)),
    )

    async def register(self, ctx):
        ctx.management.expose(self.read, self.read_config)
        ctx.management.expose(self.write, self.write_config)

    async def read_config(self, ctx, _payload):
        return await ctx.storage.get("config") or {"config": {"label": ""}, "revision": 0}

    async def write_config(self, ctx, payload):
        before = await ctx.storage.get("config")
        revision = (before or {}).get("revision", 0)
        label = payload.get("config", {}).get("label")
        if not isinstance(label, str) or not 1 <= len(label) <= 100:
            raise ValueError("A fixture label is required.")
        after = {"config": {"label": label}, "revision": revision + 1}
        if payload.get("expectedRevision") != revision or not await ctx.storage.compare_and_set("config", before, after):
            raise ManagementConflictError("Fixture configuration changed.")
        return after

    async def enable(self, ctx): pass
    async def disable(self, ctx): pass
    async def start(self, ctx): pass
    async def stop(self, ctx): pass
    async def health_check(self, ctx): return SkillHealth("PASS", "Fixture ready.")


def tenant(ident, manager_id):
    guild = SimpleNamespace(id=ident, name=f"Fixture guild {ident}", member_count=2)
    users = {actor: SimpleNamespace(guild=guild, guild_permissions=SimpleNamespace(administrator=False, manage_guild=manage))
             for actor, manage in ((manager_id, True), (90, False))}
    guild.get_member = users.get

    async def fetch_member(actor):
        return users.get(actor)

    guild.fetch_member = fetch_member
    return guild


async def app():
    guilds = {123: tenant(123, 50), 456: tenant(456, 60)}
    bot = SimpleNamespace(guilds=list(guilds.values()), get_guild=guilds.get)
    runtime = GamerHQSkillRuntime(bot)
    runtime.register(FixtureSkill())
    runtime.record_external_unavailable("missing-fixture")
    await runtime.register_all()
    return create_web_api_app(platform_service=GamerHQWebPlatformService(bot=bot, skill_runtime=runtime),
                              shared_secret=os.environ["GAMERHQ_HOST_API_SECRET"])


if __name__ == "__main__":
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("This fixture is restricted to disposable CI.")
    with tempfile.TemporaryDirectory(prefix="gamerhq-web-fixture-") as directory:
        db.DB_PATH = Path(directory) / "fixture.sqlite"
        db.init_db()
        bind = "0.0.0.0" if Path("/.dockerenv").is_file() else "127.0.0.1"
        web.run_app(app(), host=bind, port=8080, access_log=None, print=None)
