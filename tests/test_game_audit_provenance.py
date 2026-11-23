"""Source evidence never substitutes for immutable build provenance."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import game_audit_operator as operator
from tools import game_audit_provenance as provenance


def row(body):
    return {'sha256': hashlib.sha256(body).hexdigest(),
            'gitBlob': hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()}


@pytest.fixture
def public_history(tmp_path):
    def git(*args, input=None):
        return subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@example.invalid', *args], input=input,
            check=True, capture_output=True, timeout=20).stdout
    git('init', '--quiet')
    bodies = {'bot.py': b'print("fixture")\n', 'config.py': b'GUILD_ID=0\n',
              'services/games.py': b'READ_ONLY=True\n'}
    for name, body in bodies.items():
        p = tmp_path/name; p.parent.mkdir(exist_ok=True); p.write_bytes(body)
    git('add', '.'); git('commit', '--quiet', '-m', 'legacy image without revision metadata')
    first = git('rev-parse', 'HEAD').strip().decode()
    (tmp_path/'README.md').write_text('Documentation-only next commit')
    git('add', 'README.md'); git('commit', '--quiet', '-m', 'docs')
    second = git('rev-parse', 'HEAD').strip().decode()
    return git, [first, second], {k: row(v) for k,v in bodies.items()}


def test_identical_sources_never_select_a_commit_by_recency(public_history):
    git, commits, observed = public_history
    assert provenance.source_matches(git, commits, observed) == sorted(commits)
    receipt = provenance.evidence({'id': 'a'*64, 'image': 'sha256:'+'b'*64},
        {'files': observed, 'buildMarkerState':'ABSENT'}, commits, {'main': commits[-1]})
    assert receipt['hostRevisionVerified'] is False
    assert receipt['buildOriginVerified'] is False
    assert receipt['collectionExecuted'] is False
    assert receipt['matchingCommitCount'] == 2
    assert 'image' not in receipt and 'id' not in receipt
    assert 'bot.py' not in json.dumps(receipt)


@pytest.mark.parametrize('change', ['modified', 'missing', 'extra'])
def test_unknown_or_modified_source_has_no_false_match(public_history, change):
    git, commits, observed = public_history
    if change == 'modified': observed['config.py'] = row(b'different\n')
    elif change == 'missing': del observed['services/games.py']
    else: observed['services/unknown.py'] = row(b'unknown\n')
    assert provenance.source_matches(git, commits, observed) == []


def test_sha256_is_checked_independently_of_git_shortlist(public_history):
    git, commits, observed = public_history
    observed['bot.py']['sha256'] = 'f'*64
    with pytest.raises(ValueError, match='PUBLIC_SOURCE_REJECTED'):
        provenance.source_matches(git, commits, observed)


@pytest.mark.parametrize('target', ['/app', '/', '/app/services', '/app/services/games.py', '/app/data'])
def test_source_mounts_cannot_masquerade_as_image_contents(target):
    with pytest.raises(ValueError, match='SOURCE_MOUNT_REJECTED'):
        provenance.verify_mounts({'mounts': [{'Destination': target}]})


def test_authoritative_runtime_database_mount_is_not_inspected():
    provenance.verify_mounts({'mounts': [{'Destination': '/app/runtime/data'}, {'Destination': '/app/backups'}]})
    assert not provenance.selected('runtime/data/gamerhq.db')
    assert not provenance.selected('database/runtime.sqlite')
    assert not provenance.selected('.env')
    assert not provenance.selected('services/__pycache__/a.py')


@pytest.mark.skipif(os.name == 'nt', reason='Production Linux non-root no-follow probe')
@pytest.mark.parametrize('marker,expected', [(None,'ABSENT'),(b'unknown\n','UNKNOWN_DEFAULT'),(b'a'*40+b'\n','VALID_HEX'),(b'not-a-commit','INVALID')])
def test_probe_is_source_only_and_non_mutating(tmp_path, marker, expected):
    (tmp_path/'bot.py').write_bytes(b'fixture\n')
    (tmp_path/'config.py').write_bytes(b'fixture\n')
    (tmp_path/'runtime').mkdir()
    (tmp_path/'runtime/private.db').write_bytes(b'not a real database; never open')
    (tmp_path/'.env').write_bytes(b'never read')
    if marker is not None: (tmp_path/'BUILD_COMMIT').write_bytes(marker)
    body = provenance.PROBE.replace("root=Path('/app')", 'root=Path('+repr(str(tmp_path))+')')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', body], capture_output=True,
                            text=True, check=True, timeout=10)
    value = json.loads(result.stdout)
    assert value['buildMarkerState'] == expected
    assert set(value['files']) == {'bot.py', 'config.py'}
    assert value['files']['bot.py'] == row(b'fixture\n')
    assert 'never' not in result.stdout
    assert 'sqlite' not in provenance.PROBE and 'os.environ' not in provenance.PROBE
    assert 'import config' not in provenance.PROBE
    assert (tmp_path/'.env').read_bytes() == b'never read'
    assert set(p.name for p in tmp_path.iterdir()) == {'bot.py','config.py','runtime','.env'} | ({'BUILD_COMMIT'} if marker is not None else set())


@pytest.mark.skipif(os.name == 'nt', reason='Linux source symlink contract')
def test_probe_rejects_source_symlinks_without_reading_target(tmp_path):
    (tmp_path/'bot.py').write_bytes(b'fixture')
    (tmp_path/'config.py').symlink_to(tmp_path/'private.db')
    (tmp_path/'private.db').write_bytes(b'never read')
    body = provenance.PROBE.replace("root=Path('/app')", 'root=Path('+repr(str(tmp_path))+')')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', body], capture_output=True,text=True,timeout=10)
    assert result.returncode != 0 and not result.stdout and 'never read' not in result.stderr


def test_full_games_collection_still_rejects_legacy_metadata_absence(monkeypatch, tmp_path):
    # Do not execute Docker/Git or touch a real DB; drive the real existing gate.
    monkeypatch.setattr(sys, 'argv', ['observer', '--source-commit', 'a'*40,
        '--environment','production','--container','gamerhq-bot','--approval',operator.PHRASE,
        '--expected-host-commit','b'*40,'--expected-image','sha256:'+'c'*64,
        '--guild-id','1','--application-id','2','--database','/app/runtime/data/gamerhq.db',
        '--private-output',str(tmp_path/'never-created.json')])
    monkeypatch.setattr(os, 'geteuid', lambda: 0, raising=False)
    def run(command, **kwargs):
        if 'rev-parse' in command: body = b'a'*40+b'\n'
        elif 'show' in command: body = Path(operator.__file__).read_bytes()
        else: body = b''
        return type('Result', (), {'stdout': body})()
    monkeypatch.setattr(operator.subprocess, 'run', run)
    monkeypatch.setattr(operator, 'inspect', lambda _: {'hostCommit': None})
    monkeypatch.setattr(operator, 'configured_metadata', lambda _: {'database':'/app/runtime/data/gamerhq.db','buildCommit':None})
    monkeypatch.setattr(operator, 'database_mount', lambda *args: None)
    with pytest.raises(ValueError, match='HOST_REVISION_UNAVAILABLE'):
        operator.main()
    assert not (tmp_path/'never-created.json').exists()


def test_privileged_or_out_of_scope_probe_is_rejected():
    raw = {'runtimeUid': 0,'runtimeGid': 1,'buildMarkerState':'ABSENT','files': {'bot.py':row(b'a'),'config.py':row(b'b')}}
    with pytest.raises(ValueError, match='PROBE_REJECTED'): provenance.validate_probe(json.dumps(raw))
    raw['runtimeUid'] = 10001
    raw['files']['.env'] = row(b'fixture')
    with pytest.raises(ValueError, match='PROBE_REJECTED'): provenance.validate_probe(json.dumps(raw))


@pytest.mark.parametrize('changed', [None, 'image', 'id', 'source'])
def test_complete_observation_requires_same_image_container_and_source(public_history, changed):
    git, commits, observed = public_history
    original = {'id':'a'*64,'image':'sha256:'+'b'*64,'user':'gamerhq','mounts':[]}
    after = dict(original)
    if changed in ('image','id'): after[changed] = ('sha256:' if changed=='image' else '')+'c'*64
    inspections = iter((original, after))
    inspector = SimpleNamespace(inspect=lambda _:next(inspections))
    calls = []
    def run(command, **kwargs):
        assert command[:5] == ['docker','exec','--user','gamerhq',original['id']]
        assert command[5:8] == ['python','-I','-B'] and command[-1] == provenance.PROBE
        calls.append(command)
        rows = dict(observed)
        if changed == 'source' and len(calls)==2: rows['bot.py'] = row(b'changed')
        return SimpleNamespace(stdout=json.dumps({'runtimeUid':10001,'runtimeGid':10001,
            'buildMarkerState':'ABSENT','files':rows}))
    if changed:
        with pytest.raises(ValueError, match='SOURCE_CHANGED' if changed=='source' else 'CONTAINER_CHANGED'):
            provenance.collect_source_evidence(inspector,git,commits,{'main':commits[0]},run=run)
    else:
        result = provenance.collect_source_evidence(inspector,git,commits,{'main':commits[0]},run=run)
        assert result['databaseAccessed'] is False and result['discordAccessed'] is False
        assert result['containerIdentityUnchanged'] is True and result['matchingCommitCount'] == 2
    assert len(calls) == 2
