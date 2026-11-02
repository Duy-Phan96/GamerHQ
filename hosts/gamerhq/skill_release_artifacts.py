"""Reviewed GitHub Releases -> local verified wheel. Never installs or executes."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .skill_artifacts import verify_wheel, version_parts


class AssetRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.hostname not in {"api.github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
            raise ValueError("Unapproved release asset redirect.")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def github_bytes(path, *, token=None, limit=262144, binary=False):
    if not re.fullmatch(r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+", path) or ".." in path.split("/"):
        raise ValueError("Invalid release API path.")
    request = Request("https://api.github.com" + path, headers={
        "Accept": "application/octet-stream" if binary else "application/vnd.github+json", "User-Agent": "GamerHQ-Host-Artifacts"})
    if token:
        request.add_unredirected_header("Authorization", "Bearer " + token)
    with build_opener(AssetRedirects()).open(request, timeout=30) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Release input exceeds the Host limit.")
    return body if binary else json.loads(body)


def fetch_release(release, *, artifact_root, trusted_repositories, token=None, read=None):
    repository = release.get("repository", "")
    if repository not in trusted_repositories or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Release publisher is not approved by this Host profile.")
    if release.get("sourceReviewed") is not True or not re.fullmatch(r"[0-9a-f]{40}", release.get("reviewedCommit", "")):
        raise ValueError("Reviewed immutable release source is required.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", release.get("distribution", "")):
        raise ValueError("Invalid release distribution.")
    version_parts(release["version"])
    if not re.fullmatch(r"[0-9a-f]{64}", release.get("sha256", "")) or release["sha256"] == "0" * 64:
        raise ValueError("An independently reviewed wheel checksum is required.")
    read = read or (lambda path, **kwargs: github_bytes(path, token=token, **kwargs))
    root = "/repos/" + repository
    tag = release.get("tag", "v" + release["version"])
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", tag):
        raise ValueError("Invalid reviewed release tag.")
    metadata = read(root + "/releases/tags/" + tag)
    if metadata.get("tag_name") != tag or metadata.get("draft") or (metadata.get("prerelease") and not release.get("allowPrerelease")):
        raise ValueError("Release publication/channel does not match review.")
    reference = read(root + "/git/ref/tags/" + tag)["object"]
    for _ in range(4):
        if reference.get("type") != "tag":
            break
        if not re.fullmatch(r"[0-9a-f]{40}", reference.get("sha", "")):
            raise ValueError("Invalid annotated release tag.")
        reference = read(root + "/git/tags/" + reference["sha"])["object"]
    if reference.get("type") != "commit" or reference.get("sha") != release["reviewedCommit"]:
        raise ValueError("Release tag no longer identifies the reviewed source.")
    filename = release["distribution"].replace("-", "_") + "-" + release["version"] + "-py3-none-any.whl"
    def asset(name):
        matches = [value for value in metadata.get("assets", []) if value.get("name") == name]
        if len(matches) != 1 or type(matches[0].get("id")) is not int or matches[0]["id"] <= 0:
            raise ValueError("Release asset is missing or ambiguous.")
        return root + "/releases/assets/" + str(matches[0]["id"])
    sums = read(asset("SHA256SUMS"), binary=True, limit=65536).decode("utf-8")
    checksums = [match.group(1) for line in sums.splitlines()
        if (match := re.fullmatch(r"([0-9a-f]{64}) [ *]" + re.escape(filename), line))]
    if checksums != [release["sha256"]]:
        raise ValueError("Publisher checksum does not match independently reviewed bytes.")
    target = Path(artifact_root).resolve() / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError("Reviewed artifact targets cannot be symbolic links.")
    if target.exists():
        verify_wheel(target, distribution=release["distribution"], version=release["version"], sha256=release["sha256"])
        return target
    body = read(asset(filename), binary=True, limit=20 * 1024 * 1024)
    if hashlib.sha256(body).hexdigest() != release["sha256"]:
        raise ValueError("Downloaded wheel differs from reviewed bytes.")
    # Exclusive creation retains an existing artifact on races; no overwrite.
    created = False
    try:
        with target.open("xb") as output:
            created = True
            output.write(body)
        verify_wheel(target, distribution=release["distribution"], version=release["version"], sha256=release["sha256"])
    except Exception:
        # Only remove this invocation's newly created file, never another writer's.
        if created:
            target.unlink()
        raise
    return target
