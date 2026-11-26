import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import repair_staging_rc4_overlay as repair


def test_repair_changes_only_controller_identity_and_entry():
    old = repair.encode({'controllerCommit': repair.PREVIOUS, 'tenantPilotGeneration': {'id': 'active-pilot-rc4-v3'},
                         'tenantPilotActivation': {'commit': 'a' * 40}, 'tenantPilotCutover': {'fallback': 'fixed'}})
    plan = repair.make_plan('b' * 40, old, b'old', b'new')
    assert [x['name'] for x in plan['files']] == ['entry', 'policy']
    assert json.loads(bytes.fromhex(plan['files'][1]['after'])) == {**json.loads(old), 'controllerCommit': 'b' * 40}
    with pytest.raises(ValueError):
        repair.make_plan('main', old, b'old', b'new')


@pytest.mark.parametrize('boundary', ['entry', 'policy'])
def test_repair_switch_resumes_without_overwriting_unknown_bytes(boundary):
    plan = repair.make_plan('b' * 40, repair.encode({'controllerCommit': repair.PREVIOUS}), b'old', b'new')
    state = {x['name']: bytes.fromhex(x['before']) for x in plan['files']}
    def interrupt(name, body):
        state[name] = body
        if name == boundary:
            raise InterruptedError()
    with pytest.raises(InterruptedError):
        repair.switch(plan, state.__getitem__, interrupt)
    repair.switch(plan, state.__getitem__, state.__setitem__)
    assert all(state[x['name']] == bytes.fromhex(x['after']) for x in plan['files'])
    state['policy'] = b'unknown'
    before = state.copy()
    with pytest.raises(ValueError, match='CONTROLLER_BYTES_CHANGED'):
        repair.switch(plan, state.__getitem__, state.__setitem__)
    assert state == before


def test_copy_revision_only_replaces_invoked_builder(tmp_path):
    previous, target = tmp_path / 'old', tmp_path / 'new'
    path = Path('pilot-source') / repair.BUILDER
    (previous / path).parent.mkdir(parents=True)
    (previous / path).write_bytes(b'old builder')
    (previous / 'staging_automation').mkdir()
    (previous / 'staging_automation/host_promotion.py').write_bytes(b'legacy builder')
    def directory(folder, gid=None):
        if gid is not None:
            folder.mkdir(exist_ok=True)
        assert folder.is_dir() and not folder.is_symlink()
    helper = SimpleNamespace(directory=directory, checked=lambda path: path.read_bytes(),
                             atomic=lambda path, body, gid: path.write_bytes(body))
    for _ in range(2):
        repair.copy_revision(previous, target, helper, 0, b'old builder', b'new builder')
    assert (target / path).read_bytes() == b'new builder'
    assert (previous / path).read_bytes() == b'old builder'
    assert (target / 'staging_automation/host_promotion.py').read_bytes() == b'legacy builder'
    (target / path).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='REVISION_BYTES_CHANGED'):
        repair.copy_revision(previous, target, helper, 0, b'old builder', b'new builder')
