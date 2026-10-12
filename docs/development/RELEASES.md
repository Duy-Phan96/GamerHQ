# Release and Versioning Guide

This document explains development-time release expectations. Operational release
promotion remains defined by RELEASE_WORKFLOW.md, RELEASE_CHECKLIST.md, DEPLOY.md
and ROLLBACK.md.

## Branch responsibilities

- `develop` — integration branch for the next reviewed release; Host `VERSION`
  uses the next planned semantic version with a `-dev` suffix.
- `release/<version>` — frozen release candidate; Host `VERSION` ends with
  `-rcN`.
- `main` — last accepted deployable/release branch; Host `VERSION` is the exact
  accepted non-`-dev` version.
- focused feature/fix/docs/chore branches — one coherent change based on the
  agreed target branch and therefore using that target's version channel.

Normal development merges into `develop` first. Production is never deployed
from `develop`.

## GamerHQ Host version channel

The single root `VERSION` file is authoritative for the GamerHQ Host build and
runtime release identity.

Example lifecycle:

```text
develop:           1.0.0-beta.2-dev
release candidate: 1.0.0-beta.2-rc1
main/tag:           1.0.0-beta.2-rc1
```

Rules:

- `develop` must not reuse an older accepted release number;
- a release branch changes `VERSION` once to the exact candidate under review;
- a frozen candidate is not silently advanced to a newer `develop` head;
- `main` never carries a `-dev` version;
- after release planning advances, `develop` moves once to the next planned
  `-dev` version;
- Host versioning does not control SDK, Runtime API, web or external Skill
  versions.

A final beta such as `1.0.0-beta.2` may be accepted without an `-rcN` suffix.

## Semantic versioning

Use Semantic Versioning where the artifact has a reusable public compatibility surface.

### MAJOR
Breaking public contract change.

### MINOR
Backward-compatible new functionality.

### PATCH
Backward-compatible correction.

## Artifact versions are independent

Do not force one global version across the ecosystem.

Relevant version surfaces include:
- GamerHQ Host release;
- Skill SDK package;
- Runtime API version;
- each external Skill package;
- gamerhq-web deployment/release.

A Skill may release a new patch without changing the Runtime API.

## Compatibility

The existing compatibility mechanisms remain authoritative:
- Runtime API compatibility;
- SDK package version/range metadata;
- Skill manifest validation;
- external package/conformance tests;
- immutable reviewed deployment pins.

Do not invent a parallel compatibility system unless these mechanisms prove insufficient.

## Changelog

Use the existing CHANGELOG.md. For new release-oriented entries prefer concise
categories when useful: Added, Changed, Fixed, Deprecated, Removed, Security.

Do not repeat every commit. Record user-visible, operational, compatibility and
important architectural changes.

## Release readiness

For GamerHQ server releases, prepare a [Server Release Snapshot](SERVER_RELEASE_SNAPSHOT.md) for the exact GamerHQ candidate being considered. This snapshot is local to the GamerHQ repository and must not be used as a release manager for independent Skill/web repositories.

Before promotion:
- required PR checks are green;
- the branch-specific Host version channel is valid;
- database migration implications are known;
- external Skill pins are reviewed and immutable;
- configuration/environment changes are documented;
- changelog/release notes are current;
- manual Discord/VPS acceptance steps are identified;
- rollback implications are known;
- the exact release version and commit/tag are recorded;
- excluded newer/unmerged work is explicit.

## Independent repository releases

GamerHQ Host, external Skills and gamerhq-web release independently.

If this repository needs a change from another repository, create a handoff rather
than editing that repository directly. After the target repository publishes the
required version/contract, GamerHQ may update its own dependency or immutable pin.

Do not merge GamerHQ code that depends on an unavailable public contract.

## Deployment

Release completion and deployment are separate. A merged/released change is not
live until the owner executes the production deployment process and completes
post-deployment verification.

Never claim production deployment from CI alone.

## Automated release metadata checks

Release metadata is regression-tested:

- `VERSION` must match the supported semantic-version syntax;
- CI validates `develop`, candidate and `main` version channels according to the
  branch/PR target;
- the first changelog section must be the canonical `[Unreleased]` section;
- generated release notes must read from that current section;
- external Skill pins must remain reviewed and immutable.

This keeps release metadata consistent without adding a second version source or
a heavyweight release framework.
