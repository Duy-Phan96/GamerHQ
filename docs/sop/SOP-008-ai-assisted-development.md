# SOP-008: AI-Assisted Development

This is the default operating procedure for AI coding agents working on GamerHQ
or related platform repositories.

## 1. Inspect before modifying

Read selectively:
- repository README;
- AGENTS.md / repository instructions;
- architecture docs relevant to the task;
- contribution/development workflow;
- relevant implementation;
- relevant tests;
- current diff/branch state.

Do not scan unrelated private/runtime data.

## 2. Restate the task

Briefly identify current behavior, requested behavior, affected actors/components
and important constraints.

## 3. Architecture check

Determine:
- correct repository;
- correct layer;
- GamerHQ-specific vs portable;
- whether a public Runtime/SDK/API/Schema contract is involved;
- migration/backward-compatibility impact;
- dependency order across repositories.

Never solve a missing platform contract by hard-coding Skill-specific frontend
or GamerHQ-specific logic into a portable layer.

## 4. Plan

For non-trivial work provide a short plan covering modules/files, contract changes,
tests, compatibility/migration, security and deployment impact.

## 5. Implement the smallest coherent slice

Rules:
- no unrelated redesign;
- no duplicate source of truth;
- no unreviewed production action;
- no secret/private data;
- external Skills use only public SDK/Runtime contracts;
- preserve current behavior unless explicitly changed.

## 6. Test

Run focused tests first. Use full/shared gates when the change affects common
contracts, database schema, security, deployment or cross-repository integration.

Never claim a test was run when it was not.

## 7. Self-review the diff

Check ownership/layer, coupling, duplicated logic, validation, error handling,
authorization, capabilities, persistence/migrations, concurrency/retry, backwards
compatibility, security/privacy, dead code and documentation.

## 8. Human review summary

Before completion provide:
- Changed
- Why
- Tests
- Architecture
- Security
- Migration
- Risks
- Follow-ups

Keep unverified live behavior explicit.

## 9. Git / PR

Use focused commits/PRs. Do not force-push, rewrite history, delete branches/resources
or deploy production unless the owner explicitly requests that action and the
repository workflow allows it.

PR descriptions should be understandable without reading the full chat history.

## 10. Cross-repository work

Merge in dependency order:
Runtime/SDK contract → external Skill → GamerHQ pin/integration → web client.

Pin external packages to reviewed immutable versions/commits according to the
existing package workflow.

## Definition of Done

Use the central SDLC Definition of Done.

AI-generated code is not complete simply because it compiles.
