# CI Quality Gates

GamerHQ CI is a merge safety net, not a production deployment system.

## Blocking gates

The main repository currently requires:

- dependency installation from reviewed lock files;
- `pip check` dependency integrity;
- Python compilation of portable platform packages;
- buildability of the `gamerhq-skill-sdk` wheel;
- release metadata / external Skill pin preflight;
- full pytest suite on Python 3.12 and 3.14;
- repository/history safety audit;
- shell syntax validation for production scripts;
- credential-free Docker Compose validation;
- production Docker image build on Python 3.12.

These checks are intentionally offline and do not require Discord credentials.

## Why compile + wheel build are explicit

Pytest exercises behavior, but a reusable SDK also needs packaging guarantees.

The compile gate catches syntax/import-time issues in the portable platform
surface, while the wheel gate verifies that the SDK package metadata and
setuptools package selection can actually produce a distributable artifact.

## Manual gates that remain manual

CI does not prove:

- Discord permission inheritance;
- bot role hierarchy in a real guild;
- production OAuth redirect/network configuration;
- VPS/SELinux mount behavior;
- external provider availability;
- live database backup/restore correctness.

Those checks remain release/deployment acceptance steps.

## Linting and formatting policy

Do not add repository-wide formatting or lint enforcement without first
measuring the existing baseline.

Before adopting Ruff/formatter gates:

1. run the candidate tool against the current codebase;
2. classify findings into correctness vs style/churn;
3. decide whether to scope adoption first to portable Runtime/SDK code;
4. avoid a mass formatting PR mixed with product work;
5. pin the selected tool version;
6. document the migration/baseline strategy;
7. make the gate blocking only after the accepted baseline is clean.

Ruff is the preferred first tool to evaluate because it can cover focused
linting and formatting with low tooling overhead, but it is not yet a required
repository dependency.

## Static typing policy

Do not introduce project-wide strict typing as a one-step migration.

If static typing is adopted, prioritize public Runtime/SDK contracts and
cross-repository DTOs first, where type guarantees protect compatibility.

## CI changes

Changes to CI should themselves use normal PR review and must not:

- deploy production;
- require production secrets for ordinary tests;
- weaken security/authorization tests to make a build pass;
- hide failures through broad `continue-on-error` usage.

Temporary informational experiments should be clearly marked and removed or
promoted to a documented gate after evaluation.


## Release preflight automation

Run:

`python -m tools.release_preflight`

The command is read-only and verifies:

- `VERSION` uses the supported semantic-version syntax;
- the active changelog section is `[Unreleased]`;
- every dependency in `requirements-skills.lock` is an immutable exact pin.

Release preflight delegates external Skill dependency validation to the same
reviewed deployment-plan parser used by `tools.skill_package_plan`.

Accepted immutable forms are:

- GitHub archive URLs pinned to a full lowercase 40-character commit SHA;
- exact package-version pins using `distribution==version`.

Moving branches/tags, short SHAs, ranges and normalized duplicate distribution
names fail closed.

The preflight never downloads packages, reads production configuration, changes
files, tags a release or deploys.

## Release readiness report

Run:

`python -m tools.release_readiness`

or for machine-readable output:

`python -m tools.release_readiness --json`

The report composes the existing release preflight and reviewed external Skill
deployment plan. It does not create a second release policy or source of truth.

It reports:

- the candidate version;
- automated release-preflight status;
- reviewed external Skill deployment metadata;
- whether the reviewed Skill set requires an image rebuild;
- whether the repository state is ready to proceed to manual acceptance.

`readyForManualAcceptance` does **not** mean released, deployed or live-tested.
Discord/VPS acceptance from `RELEASE_CHECKLIST.md` remains an explicit owner
step. The command does not download packages, create tags/releases, mutate
release metadata, read production secrets or deploy.
