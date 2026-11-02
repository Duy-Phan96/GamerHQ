import base64
import copy
import json
from io import BytesIO

import pytest

from hosts.gamerhq.skill_deployment import parse_reviewed_skill_lock
from tools.skill_release_updates import candidate_lock, discover, github_read, release_proposal

LOCK = "# approved package\nexample-skill @ https://github.com/example/skill/archive/" + "a" * 40 + ".zip\n"
PACKAGE = parse_reviewed_skill_lock(LOCK)[0]
FILENAME = "example_skill-1.0.1-py3-none-any.whl"
RELEASE = {"tag_name": "v1.0.1", "draft": False, "prerelease": False, "assets": [
    {"id": 1, "name": "SHA256SUMS"},
    {"id": 2, "name": FILENAME, "browser_download_url": "https://github.com/example/skill/releases/download/v1.0.1/" + FILENAME}]}
SUMS = "b" * 64 + "  " + FILENAME


def test_release_is_only_a_manual_checksum_pinned_proposal():
    proposal = release_proposal(PACKAGE, current_version="1.0.0", release=RELEASE, checksums=SUMS)
    assert proposal["decision"] == "manual-review-required" and not proposal["productionApproved"]
    assert "#sha256=" + "b" * 64 in proposal["requirement"]
    updated = candidate_lock(LOCK, [proposal])
    assert updated.startswith("# approved package\n")
    assert parse_reviewed_skill_lock(updated)[0].sha256 == "b" * 64
    assert release_proposal(PACKAGE, current_version="1.0.1", release=RELEASE, checksums=SUMS) is None


@pytest.mark.parametrize("change", ["draft", "prerelease", "publisher", "checksum", "ambiguous", "version"])
def test_missing_or_mismatched_evidence_fails_closed(change):
    release = copy.deepcopy(RELEASE)
    sums = SUMS
    if change in ("draft", "prerelease"):
        release[change] = True
    elif change == "publisher":
        release["assets"][1]["browser_download_url"] = "http://localhost/private"
    elif change == "checksum":
        sums = "invalid"
    elif change == "ambiguous":
        sums += "\n" + sums
    else:
        release["tag_name"] = "latest"
    with pytest.raises(ValueError):
        release_proposal(PACKAGE, current_version="1.0.0", release=release, checksums=sums)


def test_discovery_reads_only_locked_metadata_and_never_imports_release_code():
    paths = []
    def read(path, binary=False):
        paths.append(path)
        if "/contents/" in path:
            assert path.endswith("ref=" + "a" * 40)
            return {"content": base64.b64encode(b'[project]\nversion="1.0.0"').decode()}
        if path.endswith("/releases/latest"):
            return RELEASE
        assert path == "/repos/example/skill/releases/assets/1" and binary
        return SUMS
    result = discover((PACKAGE,), read=read)
    assert len(result["proposals"]) == 1 and not result["codeExecuted"]
    assert len(paths) == 3
    assert all(path.startswith("/repos/example/skill/") for path in paths)
    assert "private" not in json.dumps(result)


def test_discovery_suppresses_raw_private_errors():
    def read(*args, **kwargs):
        raise ValueError("synthetic-secret-must-not-escape")
    result = discover((PACKAGE,), read=read)
    assert not result["proposals"] and result["skipped"]
    assert "synthetic-secret" not in json.dumps(result)


def test_api_token_is_not_forwarded_to_redirected_asset_hosts(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-test-token")
    def open_request(request, timeout):
        assert request.full_url.startswith("https://api.github.com/repos/")
        assert request.unredirected_hdrs["Authorization"] == "Bearer synthetic-test-token"
        assert "Authorization" not in request.headers
        assert timeout == 20
        return BytesIO(b"checksum-data")
    monkeypatch.setattr("tools.skill_release_updates.urlopen", open_request)
    assert github_read("/repos/example/skill/releases/assets/1", binary=True) == "checksum-data"
