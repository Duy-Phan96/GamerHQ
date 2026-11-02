import hashlib
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from database import db
from hosts.gamerhq.skill_artifacts import UpdatePolicy
from hosts.gamerhq.skill_update_controller import SkillUpdateController, deployment_history
from hosts.gamerhq.skill_deployment_docker import DockerSkillDeployment
from hosts.gamerhq.web_platform import GamerHQWebPlatformService, WebPlatformAuthorizationError
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_history_authorization_happens_before_storage_access():
    service = GamerHQWebPlatformService(bot=SimpleNamespace(get_guild=lambda ident: None), skill_runtime=None)
    with patch("hosts.gamerhq.skill_update_controller.deployment_history") as history:
        with pytest.raises(WebPlatformAuthorizationError):
            asyncio.run(service.list_skill_deployments(guild_id=123, skill_id="event-manager", authorized_guild_ids=[]))
        history.assert_not_called()


def test_history_validates_package_and_returns_only_public_projection():
    runtime = SimpleNamespace(status=AsyncMock(return_value=None))
    service = GamerHQWebPlatformService(bot=SimpleNamespace(get_guild=lambda ident: object()), skill_runtime=runtime)
    with patch("hosts.gamerhq.skill_update_controller.deployment_history", return_value=[{"state": "READY"}]) as history:
        assert asyncio.run(service.list_skill_deployments(guild_id=123, skill_id="event-manager", authorized_guild_ids=[123])) == [{"state": "READY"}]
        runtime.status.assert_awaited_once_with(guild_id=123, skill_id="event-manager")
        history.assert_called_once_with(skill_id="event-manager")


class Backend:
    def __init__(self):
        self.calls = []
        self.health = [True]
        self.mutate = False
    def stage(self, **kwargs):
        self.calls.append("stage")
        release = kwargs["release"]
        return {"healthy": True, "isolated": True, "artifactSha256": release["sha256"], "image": "sha256:" + "b" * 64,
            "runtimeApi": "1", "capabilities": [], "gatewayStarted": False}
    def current_image(self): return "sha256:" + "a" * 64
    def stop(self): self.calls.append("stop")
    def activate(self, image):
        self.calls.append(("activate", image))
        if self.mutate and image.endswith("b" * 64):
            db.set_setting("synthetic-user-data", "new version wrote data")
    def healthy(self, *, expected_version):
        self.calls.append(("health", expected_version))
        return self.health.pop(0)


@pytest.fixture
def fixture():
    with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", Path(directory) / "app.sqlite"):
        db.init_db()
        root = Path(directory)
        wheel = root / "example_skill-1.0.1-py3-none-any.whl"
        with ZipFile(wheel, "w") as archive:
            archive.writestr("example_skill-1.0.1.dist-info/METADATA", "Name: example-skill\nVersion: 1.0.1\n")
        release = {"skillId": "example-skill", "distribution": "example-skill", "version": "1.0.1", "runtimeApi": "1",
            "capabilities": [], "sourceReviewed": True, "migrationMode": "none", "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        backend = Backend()
        controller = SkillUpdateController(backend, backup_root=root / "backups")
        yield controller, backend, release, wheel, root


def stage(fixture):
    controller, _, release, wheel, _ = fixture
    return controller.stage(release=release, wheel=wheel, current_version="1.0.0", current_capabilities=[], policy=UpdatePolicy(automatic_compatible=True))


def test_stage_is_not_production_approval_and_apply_stops_old_gateway_first(fixture):
    controller, backend, release, _, root = fixture
    record = stage(fixture)
    assert record["state"] == "READY" and backend.calls == ["stage"]
    with pytest.raises(PermissionError):
        controller.apply(record["id"], approval="yes")
    assert backend.calls == ["stage"]
    applied = controller.apply(record["id"], approval="APPROVE " + record["id"])
    assert applied["state"] == "APPLIED"
    assert backend.calls[1:3] == ["stop", ("activate", "sha256:" + "b" * 64)]
    assert (root / "backups" / (record["id"] + ".sqlite")).exists()
    assert deployment_history(skill_id=release["skillId"])[0]["state"] == "APPLIED"
    assert "evidence_json" not in deployment_history(skill_id=release["skillId"])[0]


def test_failed_health_rolls_back_only_when_application_state_is_unchanged(fixture):
    controller, backend, _, _, _ = fixture
    backend.health = [False, True]
    record = stage(fixture)
    with pytest.raises(ValueError, match="health"):
        controller.apply(record["id"], approval="APPROVE " + record["id"])
    assert controller.get(record["id"])["state"] == "ROLLED_BACK"
    assert backend.calls[-2:] == [("activate", "sha256:" + "a" * 64), ("health", "1.0.0")]


def test_staged_candidate_cannot_apply_after_out_of_band_image_change(fixture):
    controller, backend, _, _, _ = fixture
    record = stage(fixture)
    with patch.object(backend, "current_image", return_value="sha256:" + "c" * 64):
        with pytest.raises(ValueError, match="changed after staging"):
            controller.apply(record["id"], approval="APPROVE " + record["id"])
    assert backend.calls == ["stage"] and controller.get(record["id"])["state"] == "READY"


def test_any_intervening_application_invalidates_other_ready_candidates(fixture):
    controller, backend, _, _, _ = fixture
    first, second = stage(fixture), stage(fixture)
    controller.apply(first["id"], approval="APPROVE " + first["id"])
    before = list(backend.calls)
    with pytest.raises(ValueError, match="changed after staging"):
        controller.apply(second["id"], approval="APPROVE " + second["id"])
    assert backend.calls == before


def test_failed_update_that_wrote_data_stops_without_unsafe_code_or_database_rollback(fixture):
    controller, backend, _, _, _ = fixture
    backend.health, backend.mutate = [False], True
    record = stage(fixture)
    with pytest.raises(ValueError):
        controller.apply(record["id"], approval="APPROVE " + record["id"])
    assert controller.get(record["id"])["state"] == "RECOVERY_REQUIRED"
    assert ("activate", "sha256:" + "a" * 64) not in backend.calls
    assert db.get_setting("synthetic-user-data") == "new version wrote data"


@pytest.mark.parametrize("change", [{"sourceReviewed": False}, {"runtimeApi": "2"}, {"capabilities": ["unknown"]}, {"migrationMode": "destructive"}])
def test_unreviewed_or_incompatible_releases_never_reach_stage_executor(fixture, change):
    controller, backend, release, wheel, _ = fixture
    with pytest.raises(ValueError):
        controller.stage(release={**release, **change}, wheel=wheel, current_version="1.0.0", current_capabilities=[], manual_review=True)
    assert backend.calls == []


def test_docker_stage_has_no_production_mounts_credentials_network_or_gateway(fixture):
    _, _, release, wheel, root = fixture
    sdk = root / "gamerhq_skill_sdk-0.3.0-py3-none-any.whl"
    with ZipFile(sdk, "w") as archive:
        archive.writestr("gamerhq_skill_sdk-0.3.0.dist-info/METADATA", "Name: gamerhq-skill-sdk\nVersion: 0.3.0\n")
    calls = []
    def run(args):
        calls.append(args)
        if args[1:3] == ["image", "inspect"]:
            return "sha256:" + "a" * 64
        if args[1] == "build":
            Path(args[args.index("--iidfile") + 1]).write_text("sha256:" + "b" * 64)
            return ""
        return json.dumps({"healthy": True, "artifactSha256": release["sha256"]})
    backend = DockerSkillDeployment(base_image="sha256:" + "a" * 64, sdk_wheel=sdk, sdk_version="0.3.0",
        sdk_sha256=hashlib.sha256(sdk.read_bytes()).hexdigest(), compose_file=root / "compose.yaml",
        service="gamerhq", container="gamerhq-bot", work_root=root / "stage", run=run)
    assert backend.stage(release=release, wheel=wheel, deployment_id="test")["isolated"]
    command = calls[-1]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--cap-drop" in command
    assert "--env-file" not in command and "--mount" not in command and "--volume" not in command
    assert "tools.skill_image_probe" in command and "bot.py" not in command
