# GamerHQ Ecosystem Cross-Project Release Rules

These rules apply to the whole GamerHQ ecosystem, including:

- `Duy-Phan96/GamerHQ` (Host / Runtime / SDK / server application);
- `gamerhq-web`;
- every standalone `gamerhq-skill-*` repository;
- future GamerHQ platform repositories that integrate with the Host.

GamerHQ is the first/reference customer of the platform, not the architectural
boundary. Repositories may evolve independently, but production deployment is a
single integrated-system decision.

## 1. Feature completion is not deployment approval

A repository may complete all of its own work successfully:

- focused tests pass;
- repository CI is green;
- PR is reviewed and merged;
- package/release is valid.

That does **not** mean the GamerHQ production server should update.

Each project reports only its deployment impact, for example:

- no server impact;
- include in next GamerHQ release;
- requires a new immutable Skill pin;
- requires a Host/Runtime contract first;
- requires migration/configuration;
- requires manual live acceptance.

No feature/Skill/web project should recommend:

- "deploy latest develop";
- "update to the newest commit";
- "all PRs are green, so production can update."

## 2. GamerHQ release candidate is the integration boundary

The GamerHQ Host repository owns the final integrated server release decision.

Before a server update is recommended, create one reviewed release candidate
that freezes the complete intended system state.

The release candidate must have an immutable identity:

- GamerHQ version;
- exact 40-character GamerHQ commit SHA;
- release-candidate PR;
- final tag once accepted.

The moving `develop` branch is never the deployment identity.

## 3. Every server update needs a Server Release Snapshot

Use:

`docs/development/SERVER_RELEASE_SNAPSHOT.md`

The snapshot must record at least:

- exact GamerHQ version and SHA;
- release-candidate PR;
- previous accepted release;
- important included PRs/change range;
- immutable external Skill pins;
- Runtime/SDK compatibility implications;
- database/data migration impact;
- environment/config/secret changes;
- Discord resource migration/repair requirements;
- image rebuild requirement;
- automated CI/readiness evidence;
- required manual Discord/VPS acceptance;
- rollback target;
- newer/unmerged work explicitly excluded.

This snapshot answers the concrete question:

> Can this exact integrated version be deployed to the GamerHQ server?

## 4. Cross-repository dependency order

When work spans repositories, use the dependency order that matches ownership:

1. GamerHQ public Runtime / SDK / Host contract;
2. external Skill/provider implementation;
3. immutable reviewed external package release/commit;
4. GamerHQ Host integration and Skill pin;
5. gamerhq-web or another client consuming the Host contract;
6. GamerHQ release candidate / integrated acceptance.

Do not create artificial handoffs when a small cross-repository change is clearly
part of the approved task, but every repository still receives its own branch,
PR, CI and merge history.

## 5. External Skills

External Skills remain independently versioned.

A Skill project may say:

`READY FOR GAMERHQ INTEGRATION`

when its own package, tests, conformance and CI are green.

It may not say:

`SERVER UPDATE RECOMMENDED`

solely because the Skill is ready.

After review, GamerHQ pins one immutable commit/release in
`requirements-skills.lock`. The GamerHQ release candidate then proves that the
complete pinned package set works together.

## 6. Parallel work and release freezing

Development does not have to stop while a release candidate is tested.

New feature work may continue on separate branches and PRs.

However:

- do not silently move the candidate to a newer `develop` head;
- do not imply newer merged/unmerged work is included;
- if the candidate intentionally absorbs new work, cut/update a new explicit
  candidate and rerun the required integration gates;
- record excluded newer work in the snapshot.

This keeps release testing reproducible even while multiple projects continue
working.

## 7. Required deployment states

Every server release decision uses exactly one of these meanings.

### BLOCKED

Do not deploy.

State the exact blocker, such as:

- failed CI;
- incompatible Skill pin;
- unresolved migration;
- missing secret/config change;
- failed release preflight;
- unresolved security concern.

### READY FOR MANUAL ACCEPTANCE

Automated integrated validation is green for one exact release candidate, but
live Discord/VPS acceptance has not yet been completed.

It is safe to begin the controlled acceptance procedure for that candidate.

### ACCEPTED FOR DEPLOYMENT

The owner has completed the required manual acceptance for that exact release
candidate.

Only now should the accepted commit/tag be promoted/deployed according to the
production workflow.

## 8. Production operations remain owner-controlled

No project, Skill or AI-assisted coding task may automatically:

- deploy production;
- restart the production bot;
- modify the production database;
- modify production secrets;
- silently repair/delete Discord resources.

Deployment remains an explicit owner action under `DEPLOY.md`,
`PRODUCTION_RULES.md`, `ROLLBACK.md` and the release snapshot.

## 9. Required handoff from every GamerHQ project

When a meaningful project phase finishes, report:

- repository;
- branch;
- PR;
- merge commit or immutable package commit;
- tests/CI;
- public contract impact;
- storage/migration impact;
- deployment impact;
- required integration order;
- whether it should be included in the next GamerHQ release snapshot.

For standalone Skills, also report:

- Skill ID;
- package/distribution version;
- Runtime API;
- capabilities;
- immutable reviewed commit/release.

This information allows the Host release project to assemble an accurate
integrated release without reconstructing intent from a long commit history.

## 10. Source of truth

For deployment decisions:

- repository/PR history explains how the system changed;
- immutable Skill pins define external package inputs;
- the GamerHQ release candidate defines the intended integrated code state;
- the Server Release Snapshot defines the deployment decision;
- production DB/runtime state remains authoritative for live persisted state.

Do not use raw commit count as evidence of deployability.
