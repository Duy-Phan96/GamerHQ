"""Credential-free candidate-image conformance probe; no bot/Gateway startup."""
import argparse
import importlib.metadata
import json
from pathlib import Path
from zipfile import ZipFile

from hosts.gamerhq.skill_artifacts import verify_wheel
from skill_runtime.devtools import validate_skill_implementation
from skill_runtime.runtime.packages import discover_installed_skills


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("skill-id", "distribution", "version", "sha256", "wheel"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    verify_wheel(Path(args.wheel), distribution=args.distribution, version=args.version, sha256=args.sha256)
    distribution = importlib.metadata.distribution(args.distribution)
    if distribution.version != args.version:
        raise ValueError("Installed candidate version does not match the reviewed artifact.")
    with ZipFile(args.wheel) as wheel:
        for name in wheel.namelist():
            if name.endswith("/") or name.endswith(".dist-info/RECORD"):
                continue
            installed = distribution.locate_file(name)
            if not installed.is_file() or installed.read_bytes() != wheel.read(name):
                raise ValueError("Installed candidate bytes do not match the reviewed wheel.")
    package = discover_installed_skills((args.skill_id,))[0]
    report = validate_skill_implementation(package.skill)
    if report.version != args.version or package.distribution != args.distribution:
        raise ValueError("Candidate manifest/provenance mismatch.")
    print(json.dumps({"healthy": True, "artifactSha256": args.sha256, "runtimeApi": report.runtime_api_version,
        "capabilities": list(package.skill.manifest.permissions), "gatewayStarted": False}))


if __name__ == "__main__":
    main()
