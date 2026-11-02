"""Host-owned artifact preflight. Never imports or installs Skill code."""
from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
import hashlib
from pathlib import Path
import re
from zipfile import ZipFile


@dataclass(frozen=True)
class UpdatePolicy:
    channel: str = "stable"
    pinned_version: str | None = None
    automatic_compatible: bool = False


def version_parts(version):
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:b([1-9][0-9]*))?", version)
    if not match:
        raise ValueError("Unsupported release version.")
    major, minor, patch = map(int, match.group(1, 2, 3))
    return major, minor, patch, match.group(4)


def update_decision(current, candidate, policy: UpdatePolicy, *, migration_safe=False, capabilities_unchanged=False):
    old = version_parts(current)
    new = version_parts(candidate)
    if policy.channel not in ("stable", "beta"):
        raise ValueError("Unsupported update channel.")
    if policy.pinned_version and candidate != policy.pinned_version:
        return "blocked-by-pin"
    if new[3] and policy.channel != "beta":
        return "blocked-by-channel"
    def order(parts):
        return (*parts[:3], 1 if parts[3] is None else 0, int(parts[3] or 0))
    if order(new) <= order(old):
        return "not-newer"
    compatible = new[0] == old[0] and (new[0] != 0 or new[1] == old[1])
    if not compatible or not migration_safe or not capabilities_unchanged:
        return "manual-review-required"
    return "eligible-for-staging" if policy.automatic_compatible else "manual-review-required"


def verify_wheel(path: Path, *, distribution: str, version: str, sha256: str):
    """Validate reviewed bytes and metadata without extracting executable files.

    A checksum establishes identity, not publisher trust or code safety. The Host
    PR/CI must separately approve provenance, capabilities and migration behavior.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("A reviewed SHA-256 checksum is required.")
    version_parts(version)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Skill artifact exceeds the preflight size limit.")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != sha256:
        raise ValueError("Skill artifact checksum mismatch.")
    with ZipFile(path) as wheel:
        entries = wheel.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 50 * 1024 * 1024:
            raise ValueError("Skill artifact expands beyond the preflight size limit.")
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate artifact entries are forbidden.")
        for name in names:
            if name.startswith("/") or "\\" in name or ":" in name or ".." in name.split("/"):
                raise ValueError("Unsafe artifact path.")
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Expected exactly one wheel distribution.")
        parsed = BytesParser().parsebytes(wheel.read(metadata[0]))
        normalized = lambda value: re.sub(r"[-_.]+", "-", value).lower()
        if normalized(parsed.get("Name", "")) != normalized(distribution) or parsed.get("Version") != version:
            raise ValueError("Artifact identity does not match the reviewed release.")
        return {"distribution": distribution, "version": version, "sha256": actual,
                "requiresDist": parsed.get_all("Requires-Dist", []), "codeExecuted": False,
                "productionApproved": False}
