# Skill Ecosystem Governance

This document defines lightweight governance for first-party and community Skills.

It does not replace the Developer Guide, Authoring Contract, Conformance checks,
Review Checklist or package compatibility rules. It connects those existing
contracts to the future Marketplace and reviewed host-deployment lifecycle.

## Core principle

A Marketplace listing, a host-installed package and a guild-installed Skill are
different states.

Conceptually:

Skill source repository
→ reviewed package/release
→ Marketplace catalog listing
→ package approved/pinned for a host deployment
→ package present in that host
→ added to a guild
→ configured
→ enabled
→ healthy

Do not collapse these states into one boolean or one approval step.

## Platform neutrality

GamerHQ is the first reference customer and default gaming use case, not the
definition of the platform.

A portable Skill must be suitable for any compatible host/community unless the
Skill itself explicitly targets a narrower domain.

Marketplace/review policy must not require GamerHQ-specific channels, roles, DB
tables, cogs or business rules.

## Publisher classes

### Official

Maintained by the platform/GamerHQ project owner or an explicitly trusted
first-party repository.

Official does not bypass:
- capability review;
- compatibility checks;
- CI;
- versioning;
- immutable deployment pins;
- security review.

### Community

Maintained by an external/community developer.

Community Skills use the same public Runtime/SDK contracts and must satisfy the
same portability and capability rules.

Community status is not equivalent to unsafe, but the platform should make the
publisher/source clear to users.

## Skill review states

Use these concepts rather than one generic Approved flag:

### Draft
Repository/Skill is still under development.

### CI Valid
Required automated package, manifest, conformance and tests pass.

CI Valid is not a security endorsement or Marketplace approval.

### Reviewed
A human/platform review confirms architecture, capabilities, security,
documentation and compatibility expectations.

### Catalog Listed
The Skill is discoverable in the Marketplace catalog.

A listed Skill may still be unavailable in a particular host deployment.

### Host Deployment Approved
A specific immutable Skill artifact/commit/version has been reviewed and pinned
for one host deployment/release.

### Deprecated
No longer recommended for new installations. Existing installations/config/data
remain readable unless a separately reviewed migration/removal process exists.

### Removed / Quarantined
Not offered for new installation. Removal from a catalog or future host build
must not silently delete guild configuration/history.

## Minimum publication metadata

A Marketplace-ready Skill should have:

- stable Skill ID;
- package/distribution name;
- display name and description;
- publisher identity/name;
- semantic Skill version;
- Runtime API version;
- SDK compatibility range;
- static capability list;
- package entry point;
- source repository;
- license status;
- documentation link;
- changelog/release notes;
- release status;
- security/privacy summary;
- migration/upgrade notes where relevant.

Future signing/provenance metadata may extend this list without changing the
Skill Runtime programming model.

## Review pipeline

Recommended flow:

1. Repository created from the approved template/pattern.
2. Developer implements only against public Runtime/SDK contracts.
3. Offline tests and package build pass.
4. Static source/conformance checks pass.
5. Manifest and static capability metadata match.
6. Reviewer checks requested capabilities and architectural boundaries.
7. Reviewer checks storage/scheduler/events/APIs/management contracts.
8. Security/privacy/failure behavior is reviewed.
9. Version/changelog/compatibility metadata is reviewed.
10. An immutable package artifact/commit is selected.
11. Marketplace listing may be created/updated.
12. Host deployment separately reviews/pins the artifact before code becomes available in that host.

Marketplace publication must not cause arbitrary code to execute in an already
running host.

## Capability governance

Capabilities are both a technical enforcement boundary and a user-facing review
surface.

Rules:

- request only what the released feature needs;
- capability additions are meaningful review events;
- static package metadata must match the executable manifest;
- missing host capabilities fail closed;
- Marketplace capability review should explain impact in user terms;
- a broad Discord permission must not be substituted for a missing scoped platform capability.

## Compatibility governance

Compatibility is multi-dimensional:

| Surface | Meaning | Authority |
| --- | --- | --- |
| Skill version | Skill's own behavior/config release | Skill package |
| Runtime API | Behavioral SDK contract generation | Skill manifest / Runtime |
| SDK range | SDK package versions the Skill targets | package metadata |
| Management/Event/API IDs | Versioned wire/behavior contracts | Skill + SDK validation |
| Host package pin | Exact reviewed code used by a host release | host deployment lock |

A Skill patch/minor release does not automatically require a Runtime API bump.

Breaking public Runtime behavior requires a separately versioned compatibility
decision rather than silently changing V1.

## Marketplace listing vs host deployment

The Marketplace catalog is metadata/discovery.

The host deployment is executable-code trust.

Therefore:

- catalog listing does not dynamically pip-install code into a running host;
- a host uses reviewed immutable artifacts/pins;
- Add to Server only works when the package is already available in the host;
- enabling is separate from adding/installing;
- configuration remains guild + Skill scoped.

This boundary can later be automated by trusted deployment infrastructure, but
the trust/review steps must remain explicit.

## Configuration and UI governance

Community Skills do not supply arbitrary React/JavaScript to the authenticated
platform dashboard in V1.

Preferred model:

Skill → declarative Management UI Schema → platform renderer → versioned Management APIs.

Any future custom-UI extension requires its own sandbox/trust architecture and
must not be introduced as a Marketplace shortcut.

## Data and uninstall/deprecation policy

Disable is not uninstall.

Catalog removal is not data deletion.

Package removal from a future host release is not automatic guild-data deletion.

Default rules:

- preserve namespaced Skill configuration/history;
- preserve user-owned Discord resources unless ownership/removal rules explicitly allow deletion;
- migrations should be deterministic and backwards compatible where practical;
- destructive cleanup requires explicit review/confirmation;
- deprecated Skills need upgrade/removal guidance before support ends.

## Security incident / quarantine

If a published Skill is discovered to be unsafe:

1. stop new Marketplace installation/listing where appropriate;
2. mark the affected version/status clearly;
3. identify affected immutable versions/host releases;
4. prepare a reviewed fix or remove the package from future host builds;
5. rotate/revoke any affected external credentials outside the Skill package;
6. preserve guild data unless deletion is necessary and explicitly reviewed;
7. publish safe upgrade/mitigation guidance;
8. verify the replacement through normal CI/review before re-listing.

Do not silently execute remote hotfix code.

## AI-generated Skills

A future AI Skill Builder does not create a separate trust class.

AI-generated Skills must satisfy the same:

- public-contract boundaries;
- capability declaration;
- conformance checks;
- tests;
- source review;
- security/privacy review;
- versioning;
- Marketplace review;
- immutable deployment rules.

Generated code is untrusted until reviewed just like human-written community code.

## Required review sources

Use together:

1. Developer Guide;
2. Strict Authoring Contract;
3. SDK Conformance Check;
4. Skill Review Checklist;
5. Package Compatibility;
6. this Governance document;
7. Marketplace/install lifecycle docs when publication/deployment is involved.

## Governance principle

Automate objective checks where possible, but keep human judgment for:

- whether capabilities are justified;
- whether behavior is safe/understandable;
- whether a public contract is appropriate;
- whether a Skill is ready to be represented to users as reviewed/official.

Do not turn governance into enterprise bureaucracy. The goal is predictable
trust and compatibility for a growing independent Skill ecosystem.
