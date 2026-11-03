"""Host-owned local/runner worker. Stage never starts a Gateway or mounts live data.

The protected Host profile owns paths, immutable base/SDK, capabilities and health
credentials. Portable Skills have no route to invoke this worker or Docker.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from database import db
from hosts.gamerhq.skill_artifacts import UpdatePolicy
from hosts.gamerhq.skill_deployment_docker import DockerSkillDeployment
from hosts.gamerhq.skill_update_controller import SkillUpdateController, deployment_history


def health_probe(profile, release):
    url = profile["healthUrl"].rstrip("/")
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    try:
        private = ipaddress.ip_address(hostname).is_private
    except ValueError:
        private = hostname in ("localhost", "gamerhq")
    if parsed.scheme not in ("http", "https") or not private or parsed.username or parsed.password:
        raise ValueError("Skill health must use a configured private Host API endpoint.")
    secret = os.environ.get(profile.get("healthSecretEnv", "GAMERHQ_WEB_API_SECRET"))
    if not secret:
        return False
    with db.connect() as conn:
        tenants = conn.execute("SELECT guild_id,enabled FROM skill_guild_state WHERE skill_id=? AND installed=1", (release["skillId"],)).fetchall()
    if not tenants:
        return False
    for tenant in tenants:
        request = Request(url + f"/api/v1/servers/{tenant['guild_id']}/skills/{release['skillId']}")
        request.add_unredirected_header("Authorization", "Bearer " + secret)
        request.add_unredirected_header("X-GamerHQ-Authorized-Guild-Ids", str(tenant["guild_id"]))
        try:
            with urlopen(request, timeout=10) as response:
                value = json.load(response)
            if value.get("version") != release["version"] or value.get("missingCapabilities"):
                return False
            if tenant["enabled"] and value.get("state", {}).get("healthy") is not True:
                return False
        except Exception:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--release", required=True)
    stage.add_argument("--wheel", required=True)
    stage.add_argument("--current-version", required=True)
    stage.add_argument("--manual-review", action="store_true")
    apply = commands.add_parser("apply")
    apply.add_argument("--id", required=True)
    apply.add_argument("--approval", required=True)
    history = commands.add_parser("history")
    history.add_argument("--skill-id", required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    if profile.get("environment") == "staging" and args.command != "history":
        from hosts.gamerhq.skill_staging import require_verification
        require_verification(profile)
        os.environ["GAMERHQ_STAGING_IMAGE"] = profile["baseImage"]
    db.DB_PATH = Path(profile["database"]).resolve(strict=True)
    if args.command == "history":
        print(json.dumps({"deployments": deployment_history(skill_id=args.skill_id)}))
        return
    root = Path(profile["artifactRoot"]).resolve(strict=True)
    if args.command == "stage":
        def artifact(name):
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                raise ValueError("Artifact inputs must be filenames inside the protected artifact directory.")
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("Artifact resolves outside the protected directory.")
            return path
        release = json.loads(artifact(args.release).read_text(encoding="utf-8"))
        wheel = artifact(args.wheel)
    else:
        if not re.fullmatch(r"[0-9a-f]{32}", args.id):
            raise ValueError("Invalid deployment identity.")
        with db.connect() as conn:
            row = conn.execute("SELECT evidence_json FROM skill_deployments WHERE id=?", (args.id,)).fetchone()
        if not row:
            raise ValueError("Deployment does not exist.")
        release = json.loads(row[0])["release"]
        if profile.get("environment") == "production" and os.environ.get("GAMERHQ_PRODUCTION_APPROVAL") != args.id:
            raise PermissionError("Protected production approval for this deployment is required.")
    approved = profile.get("approvedCapabilities", {}).get(release["skillId"], [])
    if profile.get("environment") == "staging" and release != json.loads(Path(profile["releaseFile"]).read_text(encoding="utf-8")):
        raise ValueError("Staging release differs from its verified protected descriptor.")
    if not set(release.get("capabilities", [])) <= set(approved):
        raise ValueError("Host profile does not approve these Skill capabilities.")
    backend = DockerSkillDeployment(base_image=profile["baseImage"], sdk_wheel=root / profile["sdkWheel"],
        sdk_version=profile["sdkVersion"], sdk_sha256=profile["sdkSha256"], compose_file=profile["composeFile"],
        service=profile["service"], container=profile["container"], work_root=profile["workRoot"],
        health_probe=lambda version: health_probe(profile, {**release, "version": version}))
    controller = SkillUpdateController(backend, backup_root=profile["backupRoot"])
    if args.command == "stage":
        result = controller.stage(release=release, wheel=wheel, current_version=args.current_version,
            current_capabilities=approved, policy=UpdatePolicy(**profile.get("updatePolicy", {})), manual_review=args.manual_review)
    else:
        result = controller.apply(args.id, approval=args.approval)
    print(json.dumps({"id": result["id"], "state": result["state"], "detail": result["detail"]}))


if __name__ == "__main__":
    from tools.staging_errors import StagingFailure
    try:
        main()
    except StagingFailure as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit("Host Skill worker failed. Review the private deployment record and validated profile; no credentials were logged.") from None
