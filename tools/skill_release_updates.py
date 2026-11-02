"""Prepare Host-owned reviewed Skill release update proposals, never deployments.

Only repositories already present in requirements-skills.lock are consulted.
No release code is downloaded, imported or executed. Missing evidence is a skip,
not permission to fall back to a moving branch or arbitrary source archive.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from hosts.gamerhq.skill_artifacts import UpdatePolicy, update_decision
from hosts.gamerhq.skill_deployment import load_reviewed_skill_lock, parse_reviewed_skill_lock


def release_proposal(package, *, current_version, release, checksums):
    repository = package.source_repository
    if not repository or release.get("draft") or release.get("prerelease"):
        raise ValueError("Only published stable releases from a locked repository are eligible.")
    tag = release.get("tag_name", "")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("Release tag must identify a version.")
    version = tag[1:]
    decision = update_decision(current_version, version, UpdatePolicy())
    if decision == "not-newer":
        return None
    if decision in ("blocked-by-channel", "blocked-by-pin"):
        return None
    filename = f"{package.distribution.replace('-', '_')}-{version}-py3-none-any.whl"
    assets = [asset for asset in release.get("assets", []) if asset.get("name") == filename]
    if len(assets) != 1:
        raise ValueError("Release requires exactly one matching universal wheel.")
    url = f"https://github.com/{repository}/releases/download/{tag}/{filename}"
    if assets[0].get("browser_download_url") != url:
        raise ValueError("Release artifact must belong to the locked publisher repository.")
    matches = []
    for line in checksums.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([A-Za-z0-9_.-]+)", line)
        if match and match.group(2) == filename:
            matches.append(match.group(1))
    if len(matches) != 1:
        raise ValueError("Release checksum evidence is missing or ambiguous.")
    requirement = f"{package.distribution} @ {url}#sha256={matches[0]}"
    parsed = parse_reviewed_skill_lock(requirement)[0]
    return {"distribution": package.distribution, "currentVersion": current_version,
            "candidateVersion": version, "repository": repository, "requirement": parsed.requirement,
            "decision": "manual-review-required", "productionApproved": False,
            "evidenceRequired": ["publisher/source review", "dependency/capability compatibility", "migration/backup/rollback",
                                 "artifact preflight", "staged image CI and health checks"]}


def github_read(path, *, binary=False):
    if not path.startswith("/repos/") or ".." in path.split("/"):
        raise ValueError("Only repository-scoped GitHub API requests are supported.")
    headers = {"Accept": "application/octet-stream" if binary else "application/vnd.github+json",
               "User-Agent": "GamerHQ-Host-Release-Proposals"}
    token = os.environ.get("GITHUB_TOKEN")
    request = Request("https://api.github.com" + path, headers=headers)
    if token:
        # Signed asset downloads redirect to a CDN. Never forward API credentials.
        request.add_unredirected_header("Authorization", "Bearer " + token)
    with urlopen(request, timeout=20) as response:
        body = response.read(256 * 1024 + 1)
    if len(body) > 256 * 1024:
        raise ValueError("Release metadata exceeds the allowed size.")
    return body.decode("utf-8") if binary else json.loads(body)


def discover(packages, *, read=github_read):
    proposals, skipped = [], []
    for package in packages:
        repository = package.source_repository
        if not repository:
            skipped.append({"distribution": package.distribution, "reason": "no-locked-publisher-repository"})
            continue
        try:
            owner, name = repository.split("/")
            root = "/repos/" + quote(owner, safe="") + "/" + quote(name, safe="")
            version = package.exact_version
            if version is None:
                metadata = read(root + "/contents/pyproject.toml?ref=" + package.reviewed_commit)
                document = tomllib.loads(base64.b64decode(metadata["content"], validate=False).decode("utf-8"))
                version = document["project"]["version"]
            release = read(root + "/releases/latest")
            sums = [asset for asset in release.get("assets", []) if asset.get("name") == "SHA256SUMS"]
            if len(sums) != 1 or type(sums[0].get("id")) is not int or sums[0]["id"] <= 0:
                raise ValueError("Release has no unambiguous checksum asset.")
            checksums = read(root + "/releases/assets/" + str(sums[0]["id"]), binary=True)
            proposal = release_proposal(package, current_version=version, release=release, checksums=checksums)
            if proposal:
                proposals.append(proposal)
        except (HTTPError, OSError, ValueError, KeyError, TypeError):
            # Never echo HTTP bodies, credentials, private source or raw errors.
            skipped.append({"distribution": package.distribution, "reason": "release-evidence-unavailable-or-invalid"})
    return {"schemaVersion": 1, "proposals": proposals, "skipped": skipped,
            "codeExecuted": False, "productionApproved": False}


def candidate_lock(original, proposals):
    replacements = {proposal["distribution"]: proposal["requirement"] for proposal in proposals}
    lines = []
    for raw in original.splitlines():
        if raw.strip() and not raw.lstrip().startswith("#"):
            package = parse_reviewed_skill_lock(raw)[0]
            raw = replacements.get(package.distribution, raw)
        lines.append(raw)
    result = "\n".join(lines) + "\n"
    parse_reviewed_skill_lock(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("requirements-skills.lock"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-lock", action="store_true", help="Edit only the checkout lock for a review PR.")
    args = parser.parse_args()
    original = args.lock.read_text(encoding="utf-8")
    result = discover(load_reviewed_skill_lock(args.lock))
    result["proposalId"] = hashlib.sha256(json.dumps(result["proposals"], sort_keys=True).encode()).hexdigest()[:16]
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.prepare_lock and result["proposals"]:
        args.lock.write_text(candidate_lock(original, result["proposals"]), encoding="utf-8")
    print(f"Prepared {len(result['proposals'])} review proposals; skipped {len(result['skipped'])}; no deployment performed.")


if __name__ == "__main__":
    main()
