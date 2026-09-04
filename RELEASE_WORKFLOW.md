# GamerHQ Development & Release Workflow

## Branch model

- `main` — last known-good deployable release.
- `develop` — integration branch for the next release.
- `feature/<name>` — one feature or focused change.
- `fix/<name>` — one bug fix.

## Normal change

1. Start from `develop`.
2. Create a focused `feature/...` or `fix/...` branch.
3. Implement the change.
4. Update tests/checklist and `CHANGELOG.md`.
5. Test locally/test-server against a backed-up database.
6. Merge into `develop` only after the feature works.
7. Create a release candidate and run `RELEASE_CHECKLIST.md`.
8. Merge/tag on `main` only after the release candidate passes.
9. Deploy the tagged release.

## Versioning

Use semantic-style versions:

- Patch: `1.0.1` — bug fixes only.
- Minor: `1.1.0` — backwards-compatible features.
- Major: `2.0.0` — breaking changes.
- During Beta: `1.0.0-beta.N`.

Release candidates append `-rcN` until verified.

## Commit examples

```text
fix(lfg): acknowledge create-event interaction immediately
feat(events): add private event sharing
refactor(games): separate area state from game role state
docs(release): document rollback procedure
```

## Source-of-truth rule

Git source + tagged releases are the source of truth for code. The production `gamerhq.db` is the source of truth for live state. Never package production runtime data as source code.

## Repository policy

Keep the repository private during Beta. Protect `main`, require the test suite
before merging, and never force-push release tags. Before every commit, inspect
`git status` and confirm that `.env`, databases, `runtime/`, `backups/` and logs
are absent. Tags use the exact `VERSION` value prefixed with `v`, for example
`v1.0.0-beta.1-rc4`.
