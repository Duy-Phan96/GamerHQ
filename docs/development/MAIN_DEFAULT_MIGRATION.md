# Main default, develop integration — reviewed migration

Owner decision: `main` becomes the permanent GitHub default/stable branch;
`develop` remains feature integration. This document does not authorize changing
repository settings, merging the multi-guild draft, deploying, or modifying VPS,
Discord, secrets, databases, environments or branch protections.

## Read-only audit: 20 November 2026

GitHub API reports default `develop`, main `99fca88`, develop `be6e451`.
Both branches exist. Branch protection endpoints return 404 and effective branch
rules/rulesets are empty; there are **no required status checks or PR reviews
enforced at branch level**. This existing risk must not be described as protected
main. Configuring protections requires a separate explicit owner decision.
Keep current deployment-security/production/staging owner reviewers and explicit
main deployment branch policies. Do not substitute a default-branch wildcard.

The only two scheduled workflows are:

| Workflow | Current state | Prepared migration |
| --- | --- | --- |
| staging-automation-schedule.yml | Same on both branches; twice-hourly fixed main reconcile dispatch | Add main-only wrapper gate; keep cron, operation, enrollment flag and inner workflow unchanged |
| skill-release-proposals.yml | Develop only; checks out/targets implicit default | Register on main; main-only wrapper, explicit develop checkout and develop draft PR base; retain daily cron and current permissions |

The scheduled proposal tool runs from develop; no application/tool source is
copied to stable main for this migration. It proposes checksum-backed changes;
it does not deploy or approve a release. No schedule is dispatched during review.
GitHub runs schedules from the default branch, so copies do not create two cron
runs. Register on main before switching; do not create another workflow name.

| Remaining workflows | Effect of changing default |
| --- | --- |
| tests.yml | Existing push filters main/develop and PR trigger retained; Python3.12/3.14 checks still run; branch-specific test implementation remains unchanged |
| staging-automation-regression.yml, staging-prebuild-regression.yml | Existing PR/path/manual triggers retained; main/develop code differences are intentional, not merged wholesale |
| skill-sdk-release.yml | Develop only, tag-push workflow; tag source defines execution, not a new cron/manual workflow; no tags/releases created |
| staging-tenant-pilot-preview.yml | Main only; becomes discoverable on default main without another registration copy |
| staging-tenant-acceptance.yml, staging-tenant-pilot-cutover.yml, staging-tenant-pilot-activation-prepare.yml | Identical on main/develop; main-only execution, immutable clients/digests, OIDC and deployment-security reviews unchanged |
| staging-automation.yml, staging-automation-activate.yml | Identical; explicit main/gates and restricted controller unchanged |
| skill-deployment-worker.yml, skill-staging.yml | Identical; manual inputs, production owner approval and disabled production gates unchanged |
| staging-catalog-preview.yml, staging-configuration.yml, staging-live-preflight.yml, staging-web.yml | Identical; manual main-only/resource policy gates unchanged; no dispatch or Web work authorized |
| staging-host-promotion.yml | Different historical pins; preserve enrolled main workflow, never replace with develop copy |

No workflow_run, pull_request_target, repository_dispatch or default-branch
wildcard deployment is introduced. Actions are enabled (allowed_actions=all);
no repository/organization Actions permission changes are needed. A default
change must not be used to enable unattended production.

Open PR bases were inspected. #317 remains **draft -> develop**. Other existing
develop bases: #268/#245/#227/#226/#215/#151/#150. Existing main #171 stays main;
stacked #309/#244/#232 keep their current explicit bases. Do not mass-retarget.
Future feature/fix/integration PRs must explicitly select develop because the
GitHub UI will suggest main. Reviewed release PRs target main from develop;
narrow reviewed ops registrations may target main directly. The PR template
and root AGENTS.md record this distinction. Existing branch names are retained.

## Preparation and regression gates

One focused main migration PR contains only the two schedule wrappers, this
runbook, branch-target hint and read-only regression tests. Do not merge #317
into main to register it. No manual schedule dispatch is needed for validation.
Tests assert explicit integration checkout/base, one fixed reconcile schedule,
unchanged live workflow bytes/production manual owner gate and both CI branches.
The migration PR must pass main Linux CI before owner settings approval.

Before the settings change: verify main includes the reviewed proposal wrapper,
both schedules and main/default manual workflow files; record both branch SHAs,
open PR bases, environment branch policies and gates; ensure no conflicting
deployment is pending. Never change environment rules/secrets along with default.

## Exact owner setting action — only after migration PR review/merge

Open https://github.com/Duy-Phan96/GamerHQ/settings, **General -> Default branch**,
click the switch-default-branch control, select **main**, click **Update**, then
confirm the default-branch warning. Do not use Rename branch or delete either
branch. Admin permission is required. No settings have been changed by Codex.

Afterward, read-only verify repository.default_branch=main; #317 base=develop and
draft; both branches still exist; all prior PR bases unchanged; all environment
reviewers/explicit main policies and production gate values unchanged. Observe
the next naturally scheduled runs on main: proposal PRs must target develop,
and staging wrapper must dispatch only existing main reconcile. Never dispatch
a production operation to test migration. Manual protected workflows must remain
discoverable with main selected. Local clones may run `git remote set-head origin
-a` when convenient; it changes only local symbolic metadata.

If registration/CI or source pin checks fail, leave default develop. If a defect
is found after switching, obtain owner approval to select develop again through
the same setting; preserve both branch SHAs and existing PRs. Do not force-push,
reset, retarget PRs, delete workflow history, roll back a database or redeploy.

References: [GitHub default branch setting](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-branches-in-your-repository/changing-the-default-branch),
[default-only schedule/manual registration](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

## Acceptance collector prerequisite (separate from the GitHub setting)

Run 44707410369 reached TEMPLATE_PREVIEW after 22 reads but the model-only
Discord Client lacked the Host renderer's `tree` interface. Configured Core
guide destinations invoke shared staff-guide rendering before board filtering;
empty/unmanaged fixtures did not exercise it. The collector now supplies an
unregistered local metadata tree without loading cogs, syncing commands or
starting Gateway. Its live command registry remains UNVERIFIED. Command-derived
staff guide rows are explicitly rejected; an empty tree cannot validate them.
Configured public Core guide previews still use existing services/ownership and
permission checks. TEMPLATE_PROJECTION is separate from renderer failures.

The root-owned observer source pin must be revised through the existing locked,
owner-reviewed installer before a new protected read-only collection. The exact
9935969 predecessor and both older COMMITTED acceptance markers are verified and
preserved. A separate v2 marker seals source selection. No Host deployment,
schema relaxation, database/Skill/Discord write or default-branch change is part
of that source revision. PR #317 remains draft until live evidence passes.
