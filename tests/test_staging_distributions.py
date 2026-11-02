"""Capability regressions: RHEL's Python 3.9 launcher and dbus-broker are valid."""
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import prepare_staging_access as bootstrap


@pytest.mark.parametrize('identity,family', [('almalinux', 'rhel'), ('rhel', 'rhel'), ('rocky', 'rhel'),
    ('ubuntu', 'debian'), ('debian', 'debian')])
def test_distribution_does_not_execute_os_release(tmp_path, identity, family):
    release = tmp_path / 'os-release'
    release.write_text('ID="' + identity + '"\nVERSION_ID="9.8"\nNAME="$(touch /never)"\n')
    assert bootstrap.distribution(release) == {'id': identity, 'version': '9.8', 'family': family}


@pytest.mark.parametrize('provider', ['dbus.service', 'dbus-broker.service'])
def test_dbus_checks_socket_and_available_provider_not_debian_package(tmp_path, provider):
    (tmp_path / provider).write_text('[Service]\n')
    with patch.object(bootstrap.shutil, 'which', return_value='/usr/bin/dbus-broker-launch'):
        assert not bootstrap.dbus_provider((tmp_path,))
        (tmp_path / 'dbus.socket').write_text('[Socket]\n')
        assert bootstrap.dbus_provider((tmp_path,))
    with patch.object(bootstrap.shutil, 'which', return_value=None):
        assert not bootstrap.dbus_provider((tmp_path,))


def test_python_probe_creates_venv_and_runs_its_pip_and_stdlib():
    calls = []
    with patch.object(bootstrap.shutil, 'which', side_effect=lambda name: '/usr/bin/python3.12' if name == 'python3.12' else None), \
            patch.object(bootstrap, 'run', side_effect=lambda args: calls.append(args) or ''):
        assert bootstrap.python_runtime() == '/usr/bin/python3.12'
    assert calls[0][1:3] == ['-m', 'venv']
    assert calls[1][0] == calls[2][0] and Path(calls[1][0]).name == 'python'
    assert 'ssl, sqlite3' in calls[1][-1]
    assert calls[2][1:] == ['-I', '-m', 'pip', '--version']


def test_broken_venv_fails_even_when_interpreter_exists():
    with patch.object(bootstrap.shutil, 'which', return_value='/usr/bin/python3.12'), \
            patch.object(bootstrap, 'run', side_effect=RuntimeError('venv creation failed')):
        assert bootstrap.python_runtime() is None


def test_rhel_missing_capabilities_never_invoke_apt_or_dnf():
    with patch.object(bootstrap, 'distribution', return_value={'id': 'almalinux', 'version': '9.8', 'family': 'rhel'}), \
            patch.object(bootstrap.shutil, 'which', return_value=None), \
            patch.object(bootstrap, 'python_runtime', return_value=None), \
            patch.object(bootstrap, 'run') as run:
        with pytest.raises(RuntimeError, match='shadow-utils') as error:
            bootstrap.prerequisites(True)
        assert 'dbus-user-session' not in str(error.value)
        assert 'python3.12-venv' not in str(error.value)
        run.assert_not_called()


@pytest.mark.parametrize('enforce,status', [('1', 'Enforcing'), ('0', 'Permissive')])
def test_selinux_observes_existing_policy_without_changes(tmp_path, enforce, status):
    path = tmp_path / 'enforce'; path.write_text(enforce)
    with patch.object(bootstrap, 'Path', return_value=path), \
            patch.object(bootstrap.shutil, 'which', return_value='/usr/sbin/getenforce'), \
            patch.object(bootstrap, 'run', return_value=status) as run:
        assert bootstrap.selinux_status() == status
        run.assert_called_once_with(['getenforce'])


def test_selinux_mismatch_is_not_accepted(tmp_path):
    path = tmp_path / 'enforce'; path.write_text('1')
    with patch.object(bootstrap, 'Path', return_value=path), \
            patch.object(bootstrap.shutil, 'which', return_value='/usr/sbin/getenforce'), \
            patch.object(bootstrap, 'run', return_value='Permissive'):
        with pytest.raises(RuntimeError, match='could not be verified'):
            bootstrap.selinux_status()
