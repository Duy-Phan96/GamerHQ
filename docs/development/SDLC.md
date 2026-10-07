# GamerHQ Software Development Lifecycle

GamerHQ uses a lightweight software-development lifecycle designed for a small
AI-assisted project that is growing into a reusable platform.

This lifecycle does not replace the existing architecture, Skill contracts,
release workflow, production procedures or security rules. It connects them into
one repeatable development process.

## Goals

The lifecycle should keep development:

- lightweight;
- practical;
- reproducible;
- AI-friendly;
- secure;
- easy to review;
- understandable to future contributors and employers.

It should prevent:

- feature-by-feature improvisation;
- undocumented architecture changes;
- accidental coupling between portable Skills and GamerHQ internals;
- untested AI-generated changes;
- unclear release/deployment impact;
- forgotten compatibility decisions.

## Change sizes

Not every change needs the same ceremony.

### Small

Examples:

- documentation correction;
- copy change;
- narrow test repair;
- tiny maintenance fix.

Expected flow:

Idea → scoped change → focused validation → self-review → PR.

A dedicated issue is optional when the requirement is already explicit.

### Normal

Examples:

- feature;
- bug fix;
- new host capability;
- new management UI behavior;
- non-trivial refactor.

Expected flow:

Issue → architecture check → plan → implementation → tests → self-review → PR.

### Architectural

Examples:

- Runtime / SDK contract changes;
- database lifecycle changes;
- repository-boundary or public-contract changes;
- security/authentication changes;
- Marketplace/install lifecycle changes;
- external Skill compatibility changes.

Expected flow:

Issue → architecture analysis → ADR when justified → staged implementation →
repository-local tests → PR → handoff to another repository only when required.

## Lifecycle

### 1. Idea

Capture the problem or opportunity before discussing implementation details.

Typical categories:

- GamerHQ community feature;
- reusable platform capability;
- new Skill;
- bug;
- refactor;
- infrastructure;
- security;
- documentation;
- experiment.

### 2. Triage

Classify the change before coding.

Answer:

- What user/problem is affected?
- Is this GamerHQ-specific or reusable?
- Is it a bug, feature, Skill, platform capability, refactor, security change or infrastructure work?
- Is it small, normal or architectural?
- Which repository owns it?

### 3. Requirement / Issue

For normal and architectural work define:

- problem;
- desired outcome;
- user/actor;
- acceptance criteria;
- constraints;
- affected systems;
- non-goals.

GitHub Issues are the default lightweight requirements record.

### 4. Architecture Impact Check

Before implementation answer:

- Does this belong in GamerHQ host/application code?
- Does it belong in the portable Skill Runtime / SDK?
- Should it be an independent Skill?
- Does it belong in gamerhq-web?
- Does it belong in Marketplace/catalog/deployment infrastructure?
- Does it introduce GamerHQ-specific coupling into a portable layer?
- Does it change a public contract?
- Does it change persisted state?
- Does it require a migration?
- Does it affect security, authorization or capabilities?
- Does another repository need a new public contract or release?

GamerHQ is the first reference customer, not the platform boundary. Portable
components must remain usable by unrelated communities.

### 5. Implementation Plan

Before coding, produce a short plan containing:

- owning repository/layer;
- files or modules likely affected;
- contracts/interfaces changed;
- tests required;
- migration risk;
- backward-compatibility impact;
- security impact;
- deployment impact;
- required handoffs to other repositories, if any.

The plan should be proportional to the task.

### 6. Implementation

Implement the smallest coherent change.

Rules:

- avoid unrelated refactoring;
- reuse existing services/contracts instead of adding parallel systems;
- keep source-of-truth ownership explicit;
- preserve backward compatibility unless a breaking change is intentional and versioned;
- prefer additive migrations;
- keep Skills portable;
- do not put private GamerHQ implementation dependencies into external Skills;
- do not add dynamic runtime package installation as a shortcut around reviewed deployment.

### 7. Focused Testing

Run the smallest tests that exercise the change while iterating.

Bug fixes should normally include a regression test that fails before the fix.

Use existing test harnesses and offline Discord isolation.

See [TESTING.md](TESTING.md).

### 8. Full Quality Gates

Run wider checks when the change touches:

- shared Runtime/SDK contracts;
- database schema;
- authorization/security;
- deployment;
- package compatibility;
- shared host adapters;
- cross-feature helpers;
- release candidates.

CI remains the merge gate.

### 9. AI Self-Review

Before opening or updating the PR, inspect the diff as if reviewing another
developer's work.

Check specifically for:

- wrong repository/layer;
- GamerHQ/platform coupling;
- duplicated state or services;
- missing validation;
- authorization gaps;
- capability escalation;
- migration/backward-compatibility mistakes;
- error-handling gaps;
- leaked secrets/private data;
- dead code;
- unnecessary abstractions;
- incomplete tests;
- outdated documentation.

### 10. Review Summary

For AI-assisted work, provide a concise human review summary:

- **Changed:** what changed;
- **Why:** requirement/problem solved;
- **Tests:** what actually ran and the outcomes;
- **Architecture:** public contracts/layers affected;
- **Security:** material security implications;
- **Migration:** database/config/package changes;
- **Risks:** known limits or unverified live behavior;
- **Follow-ups:** intentionally deferred work.

Do not claim live Discord/VPS validation when only offline CI ran.

### 11. Pull Request

A PR should be focused and explain:

- requirement / linked Issue;
- implementation;
- architecture impact;
- tests;
- compatibility;
- migration;
- deployment impact;
- follow-ups.

Do not implement another repository's side of a dependency. Produce a handoff and wait for that repository to publish the required contract/version.

### 12. Merge

Normal feature work targets `develop`.

`main` remains the reviewed release/deployment branch.

Do not merge a dependent repository before its required public contract is
available in the owning repository.

### 13. Release

Release work follows the existing:

- `RELEASE_WORKFLOW.md`;
- `RELEASE_CHECKLIST.md`;
- version compatibility documentation.

Reusable SDKs and Skills use semantic versioning expectations.

See [RELEASES.md](RELEASES.md).

### 14. Deployment

Production deployment remains an explicit owner operation.

GamerHQ server deployment is a GamerHQ-repository concern. Use the [Server Release Snapshot](SERVER_RELEASE_SNAPSHOT.md) when preparing a GamerHQ server release. External repositories publish independently and are consumed only through reviewed released contracts/packages.

Use the existing:

- `DEPLOY.md`;
- `ROLLBACK.md`;
- `PRODUCTION_RULES.md`;
- `docs/PRODUCTION_OPERATIONS.md`;
- `scripts/update.sh`;
- `scripts/backup.sh`.

Normal code review must not silently deploy.

### 15. Verify / Monitor

After deployment verify:

- container/process health;
- expected Skill/runtime status;
- private server-log;
- relevant Discord acceptance flow;
- migrations/data integrity when applicable.

Never use logs as a reason to expose secrets or private community content.

### 16. Retrospective / Improvement

A retrospective is useful when:

- a production incident occurred;
- CI missed a regression;
- rollback was required;
- the same bug class recurs;
- architecture friction repeatedly slows development.

Do not create retrospective documents for routine successful changes.

## Definition of Done

Apply only relevant items.

A change is complete when applicable items are satisfied:

- [ ] Requirement and acceptance criteria are implemented.
- [ ] Correct repository/layer owns the behavior.
- [ ] Architecture boundaries remain intact.
- [ ] No unnecessary coupling or duplicate source of truth was introduced.
- [ ] Public contracts are versioned/updated where required.
- [ ] Tests were added or updated.
- [ ] Relevant focused tests pass.
- [ ] Required CI quality gates pass.
- [ ] Error/failure cases were considered.
- [ ] Authorization/capabilities were reviewed where relevant.
- [ ] Secrets/private data handling was reviewed where relevant.
- [ ] Documentation reflects changed public behavior.
- [ ] Configuration/environment changes are documented.
- [ ] Database migration is safe and tested where relevant.
- [ ] Backward compatibility was considered.
- [ ] AI self-review was completed.
- [ ] PR explains architecture/test/deployment impact.
- [ ] Live acceptance requirements are explicitly known.
- [ ] Deployment/rollback impact is understood.
- [ ] If this change is intended for a server update, it is represented by an exact release-candidate snapshot rather than an implicit `develop` head.

"The code works" alone is not a Definition of Done.

## Traceability

Keep traceability lightweight:

Requirement → GitHub Issue → PR → Commit → Release.

PRs should reference their Issue when one exists.

Do not introduce separate requirements-management software unless GitHub becomes
insufficient for demonstrated project needs.
