"""Registration can run on PRs; live access stays main/owner/pin gated."""
import hashlib
from pathlib import Path
import re
import subprocess
from tools.game_audit_execution import FILES

def test_games_workflow_has_no_production_or_artifact_path_and_pins_reviewed_client():
    path = Path('.github/workflows/staging-games-audit.yml')
    source = path.read_text()
    collect = source.split('  collect:\n', 1)[1]
    assert '    environment: deployment-security\n' in collect
    assert "if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in collect
    assert '      contents: read\n      id-token: write\n' in collect
    assert "    if: github.event_name == 'pull_request'\n" in source.split('  collect:\n',1)[0]
    assert 'host-staging-automation' in source
    assert 'upload-artifact' not in source
    assert 'production' not in source.lower() and 'tenant-acceptance' not in source
    pin = re.search(r'          ref: ([0-9a-f]{40})\n',collect).group(1)
    assert re.fullmatch('[0-9a-f]{40}',pin)
    assert '          persist-credentials: false\n' in collect
    for name in (*FILES,'tools/staging_automation_client.py','tools/staging_automation/games_audit.py','tools/game_audit_operator.py'):
        frozen = subprocess.run(['git','show',pin+':'+name],capture_output=True,check=True).stdout
        assert hashlib.sha256(frozen).hexdigest() == hashlib.sha256(Path(name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
