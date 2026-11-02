import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from hosts.gamerhq.skill_artifacts import UpdatePolicy, update_decision, verify_wheel
from hosts.gamerhq.skill_deployment import SkillDeploymentPlanError, parse_reviewed_skill_lock


def test_checksum_wheel_lock_roundtrips_and_mismatches_fail():
    line = "example-skill @ https://github.com/example/skill/releases/download/v1.2.3/example_skill-1.2.3-py3-none-any.whl#sha256=" + "a" * 64
    parsed = parse_reviewed_skill_lock(line)[0]
    assert parsed.requirement == line and parsed.sha256 == "a" * 64
    assert parsed.to_dict()["sourceKind"] == "release-wheel"
    for invalid in (line.replace("#sha256=", "#sha1="), line.replace("example_skill-1.2.3", "other-1.2.3"),
                    line.replace("download/v1.2.3", "download/latest"), line[:-1]):
        with pytest.raises(SkillDeploymentPlanError):
            parse_reviewed_skill_lock(invalid)


def test_artifact_verification_never_imports_package(tmp_path):
    path = tmp_path / "example_skill-1.2.3-py3-none-any.whl"
    with ZipFile(path, "w") as wheel:
        wheel.writestr("example_skill/__init__.py", "raise RuntimeError('must never execute')")
        wheel.writestr("example_skill-1.2.3.dist-info/METADATA", "Name: example-skill\nVersion: 1.2.3\nRequires-Dist: sdk>=0.3\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = verify_wheel(path, distribution="example-skill", version="1.2.3", sha256=digest)
    assert result["codeExecuted"] is False and result["productionApproved"] is False
    assert result["requiresDist"] == ["sdk>=0.3"]
    with pytest.raises(ValueError, match="checksum"):
        verify_wheel(path, distribution="example-skill", version="1.2.3", sha256="f" * 64)
    with pytest.raises(ValueError, match="identity"):
        verify_wheel(path, distribution="other", version="1.2.3", sha256=digest)


def test_unsafe_artifact_paths_rejected(tmp_path):
    path = tmp_path / "unsafe.whl"
    with ZipFile(path, "w") as wheel:
        wheel.writestr("../escape.py", "unsafe")
    with pytest.raises(ValueError, match="Unsafe"):
        verify_wheel(path, distribution="skill", version="1.0.0", sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize("current,candidate,policy,migration,caps,expected", [
    ("1.0.0", "1.0.1", UpdatePolicy(automatic_compatible=True), True, True, "eligible-for-staging"),
    ("1.0.0", "2.0.0", UpdatePolicy(automatic_compatible=True), True, True, "manual-review-required"),
    ("0.1.0", "0.2.0", UpdatePolicy(automatic_compatible=True), True, True, "manual-review-required"),
    ("1.0.0", "1.0.1b1", UpdatePolicy(), True, True, "blocked-by-channel"),
    ("1.0.0", "1.0.1", UpdatePolicy(pinned_version="1.0.0"), True, True, "blocked-by-pin"),
    ("1.0.0", "1.0.1", UpdatePolicy(automatic_compatible=True), False, True, "manual-review-required"),
    ("1.0.0", "1.0.1", UpdatePolicy(automatic_compatible=True), True, False, "manual-review-required"),
    ("1.0.1", "1.0.1", UpdatePolicy(), True, True, "not-newer"),
    ("1.0.1b1", "1.0.1", UpdatePolicy(automatic_compatible=True), True, True, "eligible-for-staging"),
])
def test_update_policy(current, candidate, policy, migration, caps, expected):
    assert update_decision(current, candidate, policy, migration_safe=migration, capabilities_unchanged=caps) == expected
