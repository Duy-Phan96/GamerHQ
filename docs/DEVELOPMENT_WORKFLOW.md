# Development workflow

Read [AGENTS.md](../AGENTS.md), then only the topic links needed for the task. This is the default workflow; [release promotion](../RELEASE_WORKFLOW.md) and [VPS deployment](../DEPLOY.md) remain separate owner operations.

## Inspect and implement

1. Check git status --short, current branch and relevant diff, including untracked files. Preserve existing work; a dirty tree is not permission to reset it.
2. Use the root topic table to find the owner module, then inspect affected code/tests. Historical *_REPORT.md and PATCH_NOTES files are optional background, not mandatory startup context.
3. Reuse the feature's resolver, permission helper, managed-message key and DB functions. Architecture is pragmatic: some cogs/services still contain direct SQL or Discord effects.
4. Change only the requested behavior. Add focused regression coverage for bugs and affected privacy, identity, retries or lifecycle. Do not add migrations/refactors merely for consistency.
5. Update the canonical topic/command/user documentation that changed; link it elsewhere instead of copying its contents.

## Environment and validation

Run from the repository root. The following commands assume the project's virtual
environment is activated; on Windows substitute .\\.venv\\Scripts\\python.exe for
python, on Linux/macOS .venv/bin/python.

Install for development when needed:
```text
python -m pip install -r requirements.lock -r requirements-dev.txt
```

| Change area | Focused test entry points |
| --- | --- |
| Structure / permissions | tests/test_onboarding.py, test_community_structure.py, test_acceptance_health.py, test_channel_adoption.py, test_channel_changes.py, test_role_settings.py |
| Pins / partners / IG | tests/test_managed_messages.py, test_support.py, test_instant_gaming.py |
| Games / voice / music | tests/test_voice_area.py, test_music_cleanup.py |
| LFG / concurrency | tests/test_lobby_management.py, test_stability.py |
| Tickets / household requests | tests/test_tickets.py, test_energy_offers.py |
| Production / repository safety | tests/test_production.py, test_repository_safety.py |

All filenames in the table are under tests/. Examples:
```text
python -m pytest tests/test_instant_gaming.py -q
python -m pytest -q
python -m pip check
python -m tools.repository_audit --history
git diff --check
git status --short
```

Run focused tests while iterating; use the full suite for shared helpers, cross-feature behavior or release preparation. Do not repeatedly rerun passing suites without new changes or unresolved risk. pytest uses tests/conftest.py to enter offline isolation; python -m tools.test is the supported unittest alternative. Raw unittest discovery bypasses that guard.

CI tests Python 3.12/3.14, dependency integrity, repository/history audit, shell syntax, credential-free Compose config and a production image build. See [.github/workflows/tests.yml](../.github/workflows/tests.yml). No dedicated linter, formatter, type checker or Markdown checker is configured. Report Docker/host/live Discord checks as unverified when unavailable.

Documentation-only changes: inspect local links/paths, check consistency against code, run diff/status and the repository audit. No full runtime suite is needed when application/test/config files are unchanged; never claim prior test results as a new run. Repository audit is heuristic, not a guarantee that no secret exists.

Do not start bot.py, use production SQLite or execute live repair/deployment to validate a normal code task. Real startup syncs Discord commands/messages and runs reconciliation. [Setup](SETUP.md) explains local configuration; [release checklist](../RELEASE_CHECKLIST.md) covers manual acceptance.

## Completion

Report changed files, behavior/docs, actual commands and outcomes, material assumptions,
known failures/risks and exact owner actions. Distinguish offline validation from
live acceptance. Leave commits/pushes to explicit owner authorization.

## Short prompts with explicit task requirements

Small change:
> Change the Gaming News default wording to [exact text]. Follow AGENTS.md and
> docs/MANAGED_MESSAGES.md. Preserve custom edits and the stored message ID.
> Run affected pin tests and git diff --check. Do not commit or push.

Normal feature:
> Add [managed feed] under [existing category]. Follow AGENTS.md,
> docs/SERVER_STRUCTURE.md and docs/PERMISSIONS.md; extend [owning service].
> Specify copy, visibility, bot access and missing-config behavior below: [requirements].
> Test repeat sync, deleted-resource recovery and unrelated overrides. No commit/push.

Bugfix:
> Reproduce [observed LFG failure] with [steps/expected result]. Follow AGENTS.md
> and docs/LFG_EVENTS.md. Add a failing regression, fix the owning rule, and run
> lobby/stability tests. Preserve private access and capacity checks. No commit/push.

Larger feature:
> Implement [feature] using AGENTS.md and docs/ARCHITECTURE.md plus [topic docs].
> Scope/non-goals: [...]. Actors/authorization: [...]. State transitions and
> persistence: [...]. Retry/migration/recovery: [...]. Acceptance examples: [...].
> Include focused and cross-feature tests and document remaining owner gates.
> Keep the detailed specification below; do not commit or push.

Permanent context lives in linked docs; task-specific requirements, safety cases
and acceptance detail still belong in the prompt. Do not shrink complex specs
just to reduce prompt length.

## Instruction/skill design decisions

Only tests/AGENTS.md adds directory-specific rules because test isolation is an
actual distinct constraint. Services/cogs ownership belongs in ARCHITECTURE.md,
not repeated directory instructions. No repository-local skills are added:
channel/pin/health/integration work is covered by the linked contracts and the
same validation workflow. Add a skill later only for a repeated, distinct
procedure that demonstrably saves work without duplicating these sources.
