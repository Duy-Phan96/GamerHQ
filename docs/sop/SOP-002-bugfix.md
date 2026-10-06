# SOP-002: Bugfix

Use for incorrect existing behavior.

## Procedure

1. Record the current behavior, environment/context and evidence without private data.
2. Define expected behavior.
3. Reproduce the bug, preferably with an automated failing regression.
4. Find the root cause and distinguish it from the visible symptom.
5. Perform an architecture check and fix the owning rule rather than adding a parallel workaround.
6. Implement the smallest safe fix.
7. Add/adjust a regression test that proves the original failure and corrected behavior.
8. Run affected integration tests and negative paths where security/permissions are involved.
9. AI self-review for broader compatibility, migration or retry/concurrency effects.
10. Open a PR explaining reproduction, root cause, fix, regression coverage and deployment/rollback impact.
11. Perform post-deployment verification only where live/external behavior requires it.

## Rule

Do not use: Guess → Patch → Hope.

Use: Reproduce → Root Cause → Regression Test → Fix → Verify.