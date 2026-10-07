# GamerHQ Development & Release Workflow

## Branch model

- `main` — last known-good deployable release.
- `develop` — integration branch for the next release.
- `feat/<name>` — one feature or focused change. Existing `feature/...` branches/history remain valid; use `feat/...` for new work.
- `fix/<name>` — one bug fix.
- `refactor/<name>` — focused internal restructuring.
- `docs/<name>` — documentation-only change.
- `test/<name>` — test-only change.
- `chore/<name>` / `ci/<name>` — maintenance or CI work.

## Normal change

1. Start from `develop`.
2. Create a focused branch using the documented `feat/`, `fix/`, `refactor/`, `docs/`, `test/`, `chore/` or `ci/` prefix.
3. Implement the change.
4. Update tests/checklist and `CHANGELOG.md`.
5. Test locally/test-server against a backed-up database.
6. Merge into `develop` only after the feature works.
7. Create a release candidate, fill the [Server Release Snapshot](docs/development/SERVER_RELEASE_SNAPSHOT.md), and run `RELEASE_CHECKLIST.md`.
8. Recommend a server update only for that exact release candidate after its automated gates are green; never recommend `latest develop`.
9. Complete the required manual Discord/VPS acceptance for that exact candidate.
10. Merge/tag on `main` only after the release candidate passes.
11. Record the release tag on the reviewed `main` commit. The VPS update script fast-forwards `main`; run it only when `origin/main` is the accepted release. See `DEPLOY.md`.

## Changelog discipline

Use the top `## [Unreleased]` section of `CHANGELOG.md` for current development.
Prefer `Added`, `Changed`, `Fixed`, `Deprecated`, `Removed` and `Security`
subsections when relevant. Historical dated Unreleased snapshots remain read-only
history; do not append new work to them.

At release time, promote the reviewed top Unreleased content into a versioned
section matching `VERSION`, then create a fresh empty top Unreleased section.

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

The repository is public. Protect `main`, require the test suite
before merging, and never force-push release tags. Before every commit, inspect
`git status` and confirm that `.env`, databases, `runtime/`, `backups/` and logs
are absent. Tags use the exact `VERSION` value prefixed with `v`, for example
`v1.0.0-beta.1-rc4`.


## Cross-project deployment rule

External Skill, web and feature projects may finish and merge independently, but they do not decide that the production server should update. Each project records its deployment impact and compatibility requirements. GamerHQ integrates those reviewed outputs into a release candidate.

The release candidate is the only server-update decision boundary. Its snapshot must record the exact GamerHQ SHA/version, external Skill pins, data/config impact, CI/readiness evidence, manual acceptance, rollback target and newer work that is intentionally excluded.

This prevents concurrent project work from turning a moving integration branch into an ambiguous deployment target.
