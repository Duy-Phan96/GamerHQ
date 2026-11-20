"""Default-branch changes must not retarget proposals or authorize deployment."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def workflow(name):
    return (ROOT/'.github/workflows'/name).read_text()


def test_release_proposals_checkout_and_target_integration_explicitly():
    text=workflow('skill-release-proposals.yml')
    assert "if: github.ref == 'refs/heads/main'" in text
    assert '          ref: develop\n' in text
    assert 'BASE_BRANCH: develop\n' in text
    assert 'repository.default_branch' not in text
    assert '--base "$BASE_BRANCH"' in text
    assert '--draft' in text
    assert 'schedule:' in text and "'30 4 * * *'" in text


def test_single_schedule_retains_fixed_staging_operation_and_main_gate():
    text=workflow('staging-automation-schedule.yml')
    assert "if: github.ref == 'refs/heads/main' && vars.GAMERHQ_STAGING_AUTOMATION_ENROLLED == 'true'" in text
    assert '--ref main -f operation=reconcile' in text
    assert text.count("cron:")==1
    assert 'production' not in text


def test_ci_checks_both_branches_and_feature_prs_once():
    text=workflow('tests.yml')
    assert 'branches: [main, develop]' in text
    assert '  pull_request:\n' in text
    assert 'branches: ["**"]' not in text
    template=(ROOT/'.github/pull_request_template.md').read_text()
    assert 'base develop explicitly' in template


def test_live_controller_workflows_are_unchanged_by_default_migration():
    for name in ('staging-automation.yml','staging-automation-activate.yml','staging-tenant-acceptance.yml',
                 'staging-tenant-pilot-cutover.yml','staging-tenant-pilot-activation-prepare.yml','skill-deployment-worker.yml'):
        original=subprocess.check_output(['git','show','9935969d1039958e667420f9dac13dcef3e8a958:.github/workflows/'+name],cwd=ROOT)
        assert (ROOT/'.github/workflows'/name).read_text().encode()==original


def test_production_remains_manual_and_owner_reviewed():
    text=workflow('skill-deployment-worker.yml')
    assert '  workflow_dispatch:' in text
    assert '  schedule:' not in text and '  push:' not in text
    assert "inputs.environment == 'production'" in text
    assert "vars.GAMERHQ_DEPLOYMENT_WORKER_ENABLED == 'true'" in text
    assert "needs['production-review'].result == 'success'" in text
    assert 'environment: deployment-security' in text
