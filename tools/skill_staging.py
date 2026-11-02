"""Protected isolated-staging operations. No production profile is accepted."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, build_opener

from hosts.gamerhq.skill_staging import validate_profile, verify_remote, require_verification, fingerprint, NoRedirects
from hosts.gamerhq.skill_release_artifacts import fetch_release
from hosts.gamerhq.skill_deployment_docker import DockerSkillDeployment, IMAGE


def release_for(profile):
    return json.loads(Path(profile["releaseFile"]).read_text(encoding="utf-8"))


def write_record(target, value):
    target = Path(target)
    if target.is_symlink():
        raise ValueError("Protected staging records cannot be symbolic links.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".new")
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(value, output, sort_keys=True)
    temporary.chmod(0o600)
    temporary.replace(target)


def host_request(profile, path, *, payload=None):
    secret = os.environ.get(profile["healthSecretEnv"])
    if not secret:
        raise ValueError("Protected staging API credential is unavailable.")
    headers = {"Content-Type": "application/json"}
    request = Request(profile["healthUrl"] + path, headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None)
    request.add_unredirected_header("Authorization", "Bearer " + secret)
    request.add_unredirected_header("X-GamerHQ-Authorized-Guild-Ids", profile["stagingGuildId"])
    with build_opener(NoRedirects()).open(request, timeout=15) as response:
        body = response.read(262145)
    if len(body) > 262144:
        raise ValueError("Staging API response exceeds its bound.")
    return json.loads(body)


def initialize(profile, approval):
    validate_profile(profile)
    if approval != "INITIALIZE ISOLATED STAGING":
        raise PermissionError("Explicit isolated staging initialization approval is required.")
    database = Path(profile["database"])
    if any(not str(profile[key]).isdigit() or int(profile[key]) <= 0 for key in ("stagingGuildId", "categoryId", "voiceCategoryId")):
        raise ValueError("Configure the dedicated staging guild/categories before initialization.")
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch(exist_ok=False)  # never replaces an existing dataset
    from database import db
    previous = db.DB_PATH
    try:
        db.DB_PATH = database
        db.init_db()
        db.set_setting(f"skill_resources:{profile['stagingGuildId']}:{release_for(profile)['skillId']}", json.dumps({
            "categoryId": int(profile["categoryId"]), "voiceCategoryId": int(profile["voiceCategoryId"]),
            "allowPublic": False, "maxResources": 30}))
    finally:
        db.DB_PATH = previous


def backend_for(profile, health):
    os.environ["GAMERHQ_STAGING_IMAGE"] = profile["baseImage"]
    return DockerSkillDeployment(base_image=profile["baseImage"], sdk_wheel=Path(profile["artifactRoot"]) / profile["sdkWheel"],
        sdk_version=profile["sdkVersion"], sdk_sha256=profile["sdkSha256"], compose_file=profile["composeFile"],
        service=profile["service"], container=profile["container"], work_root=profile["workRoot"], health_probe=health)


def bootstrap(profile, approval):
    require_verification(profile)
    if approval != "BOOTSTRAP ISOLATED STAGING":
        raise PermissionError("Explicit live staging Gateway authorization is required.")
    result = subprocess.run(["docker", "ps", "--all", "--filter", "name=^/gamerhq-staging-bot$", "--format", "{{.ID}}"],
        capture_output=True, text=True, timeout=30, check=True)
    if result.stdout.strip():
        raise ValueError("Staging already exists; use the reviewed update lifecycle, never bootstrap over it.")
    proof = json.loads((Path(profile["workRoot"]) / "initial-candidate.json").read_text())
    release = release_for(profile)
    if proof.get("fingerprint") != fingerprint(profile) or proof.get("gatewayStarted") is not False or not IMAGE.fullmatch(proof.get("image", "")):
        raise ValueError("Initial staging image is unverified or stale.")
    def available(version):
        state = host_request(profile, f"/api/v1/servers/{profile['stagingGuildId']}/skills/{release['skillId']}")
        # Availability precedes normal per-guild installation/configuration.
        return state.get("version") == version and not state.get("missingCapabilities")
    backend = backend_for(profile, available)
    try:
        backend.activate(proof["image"])
        if not backend.healthy(expected_version=release["version"]):
            raise ValueError("Initial staging Host/package health did not verify.")
    except Exception:
        backend.stop()  # only the dedicated staging service; never restore data
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("operation", choices=("inspect", "initialize", "fetch", "verify", "build", "bootstrap", "install"))
    parser.add_argument("--approval", default="")
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    validate_profile(profile)
    if args.operation == "initialize":
        initialize(profile, args.approval)
    elif args.operation == "fetch":
        release = release_for(profile)
        if profile.get("privateArtifactSource") and not os.environ.get(profile["releaseTokenEnv"]):
            raise ValueError("Approved private release access is not configured.")
        wheel = fetch_release(release, artifact_root=profile["artifactRoot"], trusted_repositories=profile["trustedRepositories"],
            token=os.environ.get(profile["releaseTokenEnv"]))
        sdk = json.loads(Path(profile["sdkReleaseFile"]).read_text(encoding="utf-8"))
        if sdk.get("distribution") != "gamerhq-skill-sdk" or sdk.get("version") != profile["sdkVersion"] or sdk.get("sha256") != profile["sdkSha256"]:
            raise ValueError("SDK release does not match the protected profile.")
        fetch_release(sdk, artifact_root=profile["artifactRoot"], trusted_repositories=profile["trustedRepositories"],
            token=os.environ.get(profile["releaseTokenEnv"]))
        descriptor = wheel.with_suffix(".release.json")
        if descriptor.exists() and json.loads(descriptor.read_text()) != release:
            raise ValueError("Existing reviewed release descriptor differs; preserve and review it.")
        if not descriptor.exists():
            with descriptor.open("x", encoding="utf-8") as output:
                json.dump(release, output, sort_keys=True)
    elif args.operation == "verify":
        proof = verify_remote(profile)
        write_record(profile["verificationFile"], proof)
    elif args.operation == "build":
        require_verification(profile)
        release = release_for(profile)
        wheel = Path(profile["artifactRoot"]) / (release["distribution"].replace("-", "_") + "-" + release["version"] + "-py3-none-any.whl")
        backend = backend_for(profile, None)
        proof = backend.stage(release=release, wheel=wheel, deployment_id="initial-staging")
        if proof.get("healthy") is not True or proof.get("isolated") is not True or proof.get("gatewayStarted") is not False or proof.get("artifactSha256") != release["sha256"] or proof.get("runtimeApi") != release["runtimeApi"] or not set(proof.get("capabilities", [])) <= set(release["capabilities"]) or not set(proof.get("capabilities", [])) <= set(profile["approvedCapabilities"].get(release["skillId"], [])):
            raise ValueError("Staging candidate does not satisfy the approved artifact/runtime/capability contract.")
        proof.update(fingerprint=fingerprint(profile), createdAt=int(time.time()))
        write_record(Path(profile["workRoot"]) / "initial-candidate.json", proof)
    elif args.operation == "bootstrap":
        bootstrap(profile, args.approval)
    elif args.operation == "install":
        require_verification(profile)
        release = release_for(profile)
        if args.approval != "INSTALL IN ISOLATED STAGING":
            raise PermissionError("Explicit staging installation authorization is required.")
        host_request(profile, f"/api/v1/servers/{profile['stagingGuildId']}/skills/{release['skillId']}/install", payload={})
    print(json.dumps({"operation": args.operation, "result": "PASS", "workerEnabled": profile.get("workerEnabled") is True,
        "productionChanged": False, "gatewayMayStart": args.operation == "bootstrap"}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Isolated staging operation failed. Review protected configuration/trust/identity evidence; no credentials were logged.") from None
