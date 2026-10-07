# Skill Ecosystem Governance

This document defines the lightweight governance model for first-party and
community Skills in the GamerHQ-compatible platform ecosystem.

It does not replace the Developer Guide, Authoring Contract, Conformance checks,
Review Checklist or package compatibility rules. It connects those documents to
the future Marketplace and reviewed host deployment workflow.

## Core principle

A Marketplace entry is not permission to execute arbitrary code.

The lifecycle is intentionally separated:

Skill repository
→ review candidate
→ approved catalog release
→ reviewed host package/deployment
→ added to a guild
→ configured
→ enabled
→ healthy

Catalog publication, package deployment, guild installation and Runtime
enablement are different states and must remain different sources of truth.

## Skill ownership classes

### Official

Maintained by the platform/GamerHQ project team.

Official does not bypass review, compatibility or capability requirements.

### Community

Maintained by an independent developer or organization.

Community Skills use the same public Runtime/SDK contracts and must not receive
special access to GamerHQ internals.

## Repository requirements

A Marketplace/review candidate should have its own reviewable repository or
package source containing at minimum:

- README with purpose and usage;
- pyproject/package metadata;
- stable Skill ID;
- semantic Skill version;
- Runtime API compatibility;
- SDK compatibility range/pin policy;
- declared static capabilities;
- package entry point;
- tests/CI;
- changelog or release notes;
- security/privacy notes where relevant;
- license/provenance information before public distribution.

Private experimental Skills may be reviewed without public distribution, but
their executable artifact/source still requires a known provenance.

## Submission states

Use a small set of human/product states:

### Draft
Not submitted. Developer-owned work in progress.

### Review candidate
Submitted for compatibility/security/product review.

### Approved
A specific Skill release/version passed the required review for catalog use.

### Published
The approved release is visible in the Marketplace catalog.

### Deprecated
Still discoverable/installed where needed, but new installations should be
discouraged because a replacement or retirement path exists.

### Blocked
Release must not be newly installed/updated because of security, compatibility
or policy risk.

These states belong to catalog/review governance. They do not replace the
Runtime states Available / Installed / Enabled / Configured / Healthy.

## Review input

Review a specific immutable Skill release, not a moving branch.

The review package should identify:

- Skill ID;
- package/distribution name;
- version;
- immutable Git commit/release artifact;
- publisher identity;
- Runtime API version;
- SDK compatibility;
- requested capabilities;
- management UI schema/version if used;
- public/management/event contracts;
- migration/upgrade behavior;
- CI result;
- known limitations.

## Required review layers

### 1. Package and identity

Use Package Compatibility and entry-point validation.

### 2. Architecture

Use the Authoring Contract and source audit.

No imports from GamerHQ application internals or another Skill's private code.

### 3. Capabilities

Requested capabilities must be explicit, minimal and explainable to an
administrator before installation.

### 4. Runtime compatibility

Runtime API and SDK compatibility must be declared and tested.

### 5. Lifecycle and persistence

Enable/start/stop/disable, storage migrations and scheduler behavior must be
retry-safe and backward compatible where applicable.

### 6. Security/privacy

Review external input, private data, Discord visibility, mentions, HTTP access,
destructive behavior and failure isolation.

### 7. Management UX

Configuration should use public Management APIs / declarative Management UI
Schema rather than private host imports.

### 8. Tests

Offline CI/conformance must pass. Live acceptance is separate when actually
required by external Discord/provider behavior.

## Approval rule

Approval applies to a specific Skill version/artifact.

A later release must be reviewed again at a level proportional to its change.

Do not treat approval of Skill ID `example-skill` as permanent approval for all
future code published under that ID.

## Change review levels

### Patch release

Usually focused compatibility/regression review when capabilities/contracts are
unchanged.

### Minor release

Review new capabilities, fields, contracts, storage changes and new external
behavior.

### Major release

Requires explicit breaking-change and migration review. Existing guild data,
scheduler jobs and API consumers must have a documented upgrade path.

Any release that adds/widens privileged capabilities receives security review
regardless of semantic version label.

## Marketplace publication

A catalog record should be declarative metadata, not executable code.

Recommended catalog metadata:

- stable Skill ID;
- display name/description;
- publisher;
- official/community classification;
- reviewed version;
- release/review status;
- Runtime API compatibility;
- SDK compatibility;
- requested capabilities;
- documentation/repository link;
- immutable artifact/commit reference;
- tags/category;
- deprecation/security notice where applicable.

Future ratings, pricing and screenshots are product metadata and must not become
security/compatibility sources of truth.

## Host package deployment

Publishing to the catalog does not dynamically install code into a GamerHQ Host.

V1 host deployment remains reviewed and explicit:

approved Skill release
→ immutable package/commit pin
→ requirements-skills.lock / deployment review
→ normal GamerHQ PR + CI + production image build

The web Add-to-Server flow may only add a Skill package already available to
the host deployment.

Runtime Git clone, arbitrary pip install, arbitrary repository URL execution or
browser-provided executable package references are forbidden.

A future automated package deployment service requires its own trust, signing,
provenance, compatibility and rollback architecture.

## Guild installation lifecycle

After the package exists in a host deployment:

Capability Review
→ Add to Server
→ Configure
→ Enable
→ Health verification

Guild installation/enablement uses authoritative host state. The Marketplace
must not create a parallel enablement/configuration database.

## Upgrades

Upgrading a deployed Skill should preserve:

- stable Skill ID;
- compatible storage/config where promised;
- persisted scheduler/job identity where promised;
- documented API/event compatibility;
- per-guild installed/enabled state.

Normal upgrade path:

new reviewed Skill release
→ update immutable host pin
→ host CI/package/image build
→ release/deploy
→ Runtime loads new version
→ Skill-owned migration/compatibility behavior
→ verify health

Do not require deleting user data for a normal compatible upgrade.

## Removal / deprecation

Removing a Skill from the catalog, a host deployment and a guild are separate
operations.

Deprecation should define:

- whether existing installations may continue;
- replacement/migration path;
- data retention/export expectations;
- scheduler cleanup behavior;
- safe host package removal requirements.

Disabling a Skill is not uninstalling it and should retain configuration by
default.

## Security response

A vulnerable release may be marked Blocked.

Response should identify:

- affected versions;
- capability/data exposure;
- fixed/replacement version if available;
- host deployments using the affected immutable artifact;
- required disable/update/deployment action;
- whether stored data or credentials require remediation.

Do not silently replace an immutable reviewed artifact behind the same version.

## Marketplace/AI Skill Builder future

An AI Skill Builder may generate code or metadata, but generated output must
enter the same repository/CI/review pipeline as human-authored Skills.

AI generation does not bypass:

- public SDK boundaries;
- capability review;
- conformance;
- tests;
- versioning;
- provenance;
- Marketplace review.

## Governance Definition of Done

A Skill release is ready for catalog approval when relevant items are satisfied:

- [ ] stable identity and package metadata;
- [ ] immutable candidate artifact/commit identified;
- [ ] semantic version and compatibility metadata;
- [ ] minimal declared capabilities;
- [ ] public SDK boundary respected;
- [ ] no forbidden host/private imports;
- [ ] lifecycle/storage/scheduler behavior reviewed;
- [ ] management contracts/schema validated;
- [ ] security/privacy reviewed;
- [ ] offline CI/conformance green;
- [ ] documentation/changelog current;
- [ ] upgrade/migration implications known;
- [ ] live acceptance requirements explicitly known;
- [ ] review decision recorded for the specific release.
