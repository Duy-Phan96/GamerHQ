# Server Release Snapshot

Use this template only for GamerHQ Host/server releases from this repository.
It is a local deployment safety record, not a cross-repository release manager.

External Skills and gamerhq-web publish independently. This snapshot records only
the exact versions/pins that this GamerHQ release chooses to consume.

## Decision

- **Release version:** `<VERSION>`
- **Release candidate PR:** `#<number>`
- **Exact GamerHQ commit:** `<40-char SHA>`
- **Target branch/tag:** `<main/tag after acceptance>`
- **Decision:** `READY FOR MANUAL ACCEPTANCE | BLOCKED | ACCEPTED FOR DEPLOYMENT`
- **Prepared at:** `<UTC timestamp>`

## Integrated scope

Summarize the important merged changes included since the previous accepted
release. Prefer PR numbers and user/operational impact over a raw commit dump.

- Previous accepted release/commit: `<version/SHA>`
- Included range: `<old SHA>..<release SHA>`
- Important included PRs:
  - `#...`
  - `#...`

Do not imply that PRs merged after the release candidate was cut are included.

## External Skills

Record every reviewed immutable Skill package installed by the release.

| Skill/distribution | Reviewed immutable commit/version | Contract/runtime impact |
| --- | --- | --- |
| | | |

The authoritative package set remains `requirements-skills.lock`.

## Data / configuration impact

- **Database schema migration:** `None | details`
- **Data migration:** `None | details`
- **Environment variables:** `None | details`
- **Secrets:** `None | details`
- **Discord resource migration/repair:** `None | details`
- **Image rebuild required:** `Yes/No`

Never infer "None" from silence. Check the included PRs and release tooling.

## Automated evidence

- Release PR CI: `PASS/FAIL`
- Python 3.12: `PASS/FAIL`
- Python 3.14: `PASS/FAIL`
- Release metadata/preflight: `PASS/FAIL`
- External Skill lock validation: `PASS/FAIL`
- Release readiness: `PASS/FAIL`
- Production preflight: `NOT RUN | PASS | FAIL`

Automated checks can make a candidate ready for manual acceptance. They do not
prove that production Discord behavior is correct.

## Manual acceptance required

List only the live checks relevant to this release plus the required baseline
health/restart checks.

- [ ] backup verified
- [ ] container/process health
- [ ] bot starts without traceback
- [ ] expected external Skills are loaded and healthy
- [ ] relevant Discord feature checks
- [ ] restart does not create duplicates or lose state
- [ ] production preflight passes

## Rollback

- Previous accepted release/tag: `<version/tag>`
- Database rollback implication: `<none/details>`
- External Skill rollback implication: `<none/details>`
- Operational rollback reference: `ROLLBACK.md`

## Excluded / later work

Explicitly list open or newer work that is **not** part of this deployment.

- `#...`

This prevents a moving `develop` branch from being confused with the reviewed
release candidate.

## Required final answer before deployment

A server update recommendation must name the immutable target and use one of
these states:

### BLOCKED

Do not deploy. State the exact failed or missing gate.

### READY FOR MANUAL ACCEPTANCE

Automated integration checks are green, but required live/VPS acceptance has not
yet been completed.

### ACCEPTED FOR DEPLOYMENT

The owner has completed the required acceptance for the exact candidate. State
the exact version/tag/SHA to deploy.

Never say only "update to the latest version", "deploy develop" or "all PRs are
green".
