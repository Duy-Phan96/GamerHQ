"""Run inside disposable AlmaLinux 9 CI; no systemd/SELinux acceptance claim."""
import sys

from tools.prepare_staging_access import distribution, python_runtime, dbus_provider


def main():
    assert sys.version_info[:2] == (3, 9), 'Keep the RHEL system Python unchanged'
    assert distribution()['family'] == 'rhel'
    assert dbus_provider(), 'RHEL user D-Bus socket/provider unavailable'
    assert python_runtime(), 'Installed Python 3.12 must actually create a working pip venv'
    assert sys.version_info[:2] == (3, 9)
    print('PASS: AlmaLinux 9 system Python 3.9, separate Python 3.12 venv/pip/SSL/SQLite, D-Bus provider. Live systemd/SELinux remain VPS checks.')


if __name__ == '__main__':
    main()
