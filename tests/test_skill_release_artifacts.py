import copy
import hashlib
from io import BytesIO
from zipfile import ZipFile
from urllib.request import Request

import pytest
from hosts.gamerhq.skill_release_artifacts import fetch_release, AssetRedirects


@pytest.fixture
def release_fixture(tmp_path):
    stream = BytesIO()
    with ZipFile(stream, "w") as wheel:
        wheel.writestr("example_skill-0.1.0.dist-info/METADATA", "Name: example-skill\nVersion: 0.1.0\n")
    body = stream.getvalue()
    release = {"repository": "owner/example-skill", "distribution": "example-skill", "version": "0.1.0",
        "sourceReviewed": True, "reviewedCommit": "a" * 40, "sha256": hashlib.sha256(body).hexdigest()}
    metadata = {"tag_name": "v0.1.0", "draft": False, "prerelease": False,
        "assets": [{"id": 1, "name": "SHA256SUMS"}, {"id": 2, "name": "example_skill-0.1.0-py3-none-any.whl"}]}
    values = {"/releases/tags/v0.1.0": metadata, "/git/ref/tags/v0.1.0": {"object": {"type": "commit", "sha": "a" * 40}},
        "/releases/assets/1": (release["sha256"] + "  example_skill-0.1.0-py3-none-any.whl\n").encode(), "/releases/assets/2": body}
    calls = []
    def read(path, **kwargs):
        calls.append(path)
        return values[path.removeprefix("/repos/owner/example-skill")]
    def fetch(**kwargs):
        return fetch_release(release, artifact_root=tmp_path, trusted_repositories=[release["repository"]], read=read, **kwargs)
    return release, values, calls, fetch, tmp_path


def test_release_bytes_are_pinned_idempotent_and_never_executed(release_fixture):
    release, values, calls, fetch, root = release_fixture
    path = fetch()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == release["sha256"]
    assert fetch() == path
    assert calls.count("/repos/owner/example-skill/releases/assets/2") == 1
    path.write_bytes(b"existing different artifact")
    with pytest.raises(ValueError):
        fetch()
    assert path.read_bytes() == b"existing different artifact"


@pytest.mark.parametrize("change", ["unreviewed", "moved-tag", "ambiguous", "checksum", "corrupt", "draft", "prerelease"])
def test_untrusted_release_evidence_never_publishes_a_local_wheel(release_fixture, change):
    release, values, _, fetch, root = release_fixture
    if change == "unreviewed": release["sourceReviewed"] = False
    if change == "moved-tag": values["/git/ref/tags/v0.1.0"]["object"]["sha"] = "b" * 40
    if change == "ambiguous": values["/releases/tags/v0.1.0"]["assets"].append({"id": 3, "name": "SHA256SUMS"})
    if change == "checksum": values["/releases/assets/1"] = b"wrong checksum"
    if change == "corrupt": values["/releases/assets/2"] = b"different wheel bytes"
    if change in ("draft", "prerelease"): values["/releases/tags/v0.1.0"][change] = True
    with pytest.raises(ValueError): fetch()
    assert not list(root.glob("*.whl"))


def test_redirect_never_forwards_the_api_credential_and_rejects_unapproved_origins():
    request = Request("https://api.github.com/repos/owner/skill/releases/assets/1")
    request.add_unredirected_header("Authorization", "Bearer fake-test-credential")
    redirected = AssetRedirects().redirect_request(request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/example")
    assert redirected.get_header("Authorization") is None
    with pytest.raises(ValueError):
        AssetRedirects().redirect_request(request, None, 302, "Found", {}, "https://unapproved.test/asset")


def test_annotated_tag_must_resolve_to_the_reviewed_commit(release_fixture):
    release, values, _, fetch, root = release_fixture
    values["/git/ref/tags/v0.1.0"] = {"object": {"type": "tag", "sha": "c" * 40}}
    values["/git/tags/" + "c" * 40] = {"object": {"type": "commit", "sha": "a" * 40}}
    assert fetch().exists()


def test_sdk_can_use_an_independent_reviewed_release_tag(release_fixture):
    release, values, _, fetch, _ = release_fixture
    release["tag"] = "skill-sdk-v0.1.0"
    values["/releases/tags/skill-sdk-v0.1.0"] = values.pop("/releases/tags/v0.1.0")
    values["/releases/tags/skill-sdk-v0.1.0"]["tag_name"] = release["tag"]
    values["/git/ref/tags/skill-sdk-v0.1.0"] = values.pop("/git/ref/tags/v0.1.0")
    assert fetch().exists()
