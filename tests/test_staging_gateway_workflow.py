import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('approval,failed_operation,expected_count,success', [
    ('BOOTSTRAP ISOLATED STAGING', '', 2, True),
    ('BOOTSTRAP ISOLATED STAGING', 'verify', 1, False),
    ('incorrect approval', '', 0, False),
])
def test_first_gateway_verifies_fresh_proof_after_review_and_stops_on_failure(tmp_path, approval, failed_operation, expected_count, success):
    bash = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else shutil.which('bash')
    if not bash or not Path(bash).is_file(): pytest.skip('Bash is required for workflow execution')
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/skill-staging.yml').read_text(encoding='utf-8')
    body = workflow.split('        run: |\n', 1)[1]
    body = '\n'.join(line[10:] if line.startswith('          ') else line for line in body.splitlines()) + '\n'
    capture, script = tmp_path / 'calls.txt', tmp_path / 'workflow.sh'
    stub = 'python3() { printf "%s\\n" "$*" >> "$CAPTURE"; if [[ -n "$FAIL_OPERATION" && " $* " == *" --operation $FAIL_OPERATION "* ]]; then return 17; fi; }\n'
    script.write_text(stub + body, encoding='utf-8', newline='\n')
    result = subprocess.run([str(bash), script.as_posix()], capture_output=True, text=True,
        env={**os.environ, 'CAPTURE': capture.as_posix(), 'FAIL_OPERATION': failed_operation,
            'OPERATION': 'bootstrap', 'APPROVAL': approval})
    calls = capture.read_text(encoding='utf-8').splitlines() if capture.exists() else []
    assert len(calls) == expected_count and (result.returncode == 0) == success
    if calls: assert '--operation verify ' in calls[0]
    if success: assert '--operation bootstrap --approval BOOTSTRAP ISOLATED STAGING' in calls[1]
    assert 'environment: deployment-security' in workflow
    assert "inputs.operation == 'bootstrap'" in workflow and 'needs: gateway-review' in workflow
