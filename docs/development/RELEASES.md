# Release and Versioning Guide

This document explains development-time release expectations. Operational release
promotion remains defined by RELEASE_WORKFLOW.md, RELEASE_CHECKLIST.md, DEPLOY.md
and ROLLBACK.md.

## Branch responsibilities

- develop — integration branch for the next reviewed release.
- main — last accepted deployable/release branch.
- focused feature/fix/docs/chore branches — one coherent change.

Normal development merges into develop first. Production is never deployed from develop.

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

A green feature branch or Skill repository is not, by itself, evidence that the combined GamerHQ server is deployable. Before any server update recommendation, prepare a [Server Release Snapshot](SERVER_RELEASE_SNAPSHOT.md) for one immutable release candidate. The snapshot is the integration decision record and must distinguish included work from newer/unmerged work.

Before promotion:
- required PR checks are green;
- database migration implications are known;
- external Skill pins are reviewed and immutable;
- configuration/environment changes are documented;
- changelog/release notes are current;
- manual Discord/VPS acceptance steps are identified;
- rollback implications are known;
- the exact release version and commit/tag are recorded;
- excluded newer/unmerged work is explicit.

## Cross-repository releases

For coordinated changes merge in dependency order:
1. public Runtime/SDK contract;
2. external Skill/provider using the contract;
3. GamerHQ reviewed Skill pin/integration;
4. gamerhq-web client consuming the host contract.

Do not merge a client that depends on an unavailable contract merely because its
own repository builds.

## Deployment

Release completion and deployment are separate. A merged/released change is not
live until the owner executes the production deployment process and completes
post-deployment verification.

Never claim production deployment from CI alone.


## Automated release metadata checks

Release metadata is regression-tested:

- `VERSION` must match the existing semantic-version syntax;
- the first changelog section must be the canonical `[Unreleased]` section;
- generated release notes must read from that current section.

This keeps release metadata consistent without adding a separate release
framework.
