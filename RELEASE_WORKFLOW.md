# GamerHQ Development & Release Workflow

## Branch model

- `main` — last known-good deployable release with an exact accepted, non-`-dev` `VERSION`.
- `develop` — integration branch for the next release; `VERSION` carries the next planned semantic version with a `-dev` suffix.
- `release/<version>` — immutable release-candidate work; `VERSION` ends with `-rcN` while the candidate is under review.
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
7. When a coherent GamerHQ server milestone is ready, create a `release/<version>` branch and change `VERSION` from the `-dev` value to the exact `-rcN` candidate. Fill the [Server Release Snapshot](docs/development/SERVER_RELEASE_SNAPSHOT.md) and run `RELEASE_CHECKLIST.md`.
8. Freeze and validate that exact candidate; do not silently advance it to a newer `develop` head.
9. Complete the required GamerHQ Discord/VPS acceptance for that exact candidate.
10. Merge/tag on `main` only after the release candidate passes. `main` must never retain a `-dev` version.
11. Record the release tag on the reviewed `main` commit. The VPS update script fast-forwards `main`; run it only when `origin/main` is the accepted release. See `DEPLOY.md`.
12. After release planning advances, update `develop` once to the next planned `-dev` version; do not reuse an older accepted release number.

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

The Host uses one `VERSION` file with branch-specific channels:

```text
develop:           1.0.0-beta.2-dev
release candidate: 1.0.0-beta.2-rc1
main/tag:           exact accepted version, for example 1.0.0-beta.2-rc1
```

A later stable beta release may use `1.0.0-beta.2` without `-rcN`. The accepted
`main` version may therefore be a reviewed release candidate or a final beta,
but never a `-dev` version.

CI calls the release preflight with the expected branch channel:

- ordinary PRs and `develop` pushes: `develop`;
- `release/*` candidate PRs: `candidate`;
- PRs/pushes targeting `main`: `main`.

Host, SDK, Runtime API, web and external Skill versions remain independent. This
policy applies only to the GamerHQ Host `VERSION` file.

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
`v1.0.0-beta.2-rc1`.
