# SOP-001: Feature Development

Use for normal product/platform features.

## Procedure

1. Capture the requirement: problem, user/actor, desired outcome, acceptance criteria and non-goals.
2. Create/reference a GitHub Issue. This is optional only for truly small/self-contained work.
3. Perform the architecture check: correct repository/layer, GamerHQ-specific vs reusable, public contracts, migration, security and deployment impact.
4. Create a focused branch from the agreed base.
5. Plan affected modules/contracts, tests, compatibility/migration risk and cross-repository ordering.
6. Implement the smallest coherent change. Avoid unrelated cleanup and reuse existing sources of truth/services/contracts.
7. Test with focused coverage while iterating and wider gates for shared/architectural changes.
8. AI self-review: inspect coupling, validation, authorization, compatibility, documentation and dead code.
9. Open a PR that links the Issue and explains architecture, testing, security and deployment impact.
10. Review/CI: fix concrete findings; do not bypass gates.
11. Merge normally to develop. Merge cross-repository dependencies first.
12. Release/deploy only when applicable and through the existing owner-controlled procedures.

## Completion summary

Report:
- Changed
- Why
- Tests
- Architecture
- Security
- Migration
- Risks
- Follow-ups