# Architecture Decision Records

Use Architecture Decision Records (ADRs) only for decisions with durable
architectural consequences.

Good ADR candidates include:
- portable Skill Runtime architecture;
- external Skill repository/package model;
- event/public/management contract design;
- web/backend authentication boundary;
- database technology or migration policy;
- Marketplace installation/deployment architecture;
- breaking compatibility/versioning decisions.

Do not create ADRs for:
- routine bug fixes;
- copy/UI tweaks;
- ordinary implementation details;
- decisions already fully governed by an existing stable contract.

## Naming

Use:

`ADR-XXX-short-title.md`

Example:

`ADR-001-external-skill-packages.md`

## Template

# ADR-XXX: Title

Status: Proposed | Accepted | Superseded | Rejected
Date: YYYY-MM-DD

## Context

What problem or architectural pressure requires a durable decision?

## Decision

What is the chosen architecture/rule?

## Alternatives Considered

What realistic alternatives were considered and why were they not chosen?

## Consequences

What becomes easier, harder or constrained?

## Migration / Compatibility

What existing state, API, Skill, deployment or user behavior must be preserved or migrated?

## Lifecycle

Link the Issue/PR that introduced or changed the decision.

An ADR records why a durable decision exists; it should not duplicate full
implementation documentation.
