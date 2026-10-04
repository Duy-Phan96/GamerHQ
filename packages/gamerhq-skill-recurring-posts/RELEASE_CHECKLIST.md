# Release Checklist — Recurring Posts Skill

Use this before tagging or pinning a release of `gamerhq-skill-recurring-posts`.

## Identity

- [ ] Skill ID remains `recurring-posts`.
- [ ] Entry-point name remains `recurring-posts`.
- [ ] Distribution remains `gamerhq-skill-recurring-posts`.
- [ ] Runtime API compatibility is reviewed.
- [ ] Skill version follows semantic versioning.

## Permissions

- [ ] `[tool.gamerhq].capabilities` exactly matches `SkillManifest.permissions`.
- [ ] Every requested capability is still required.
- [ ] No new host/private imports were introduced.

## Persistence

- [ ] `posts.v1` compatibility is preserved or migrated deterministically.
- [ ] `recurring-post.execute.v1` remains compatible with persisted jobs, or a migration plan exists.
- [ ] Existing job keys remain under `post:<post-id>`.
- [ ] Disable remains non-destructive.

## Contracts

- [ ] Management API IDs remain versioned and documented.
- [ ] Event IDs remain versioned and documented.
- [ ] Breaking payload/behavior changes use a new contract version.

## Tests

- [ ] Static source audit passes.
- [ ] Package metadata/manifest consistency passes.
- [ ] Offline unit tests pass.
- [ ] Python 3.12 CI passes.
- [ ] Python 3.14 CI passes.
- [ ] No Discord token or production state is required by tests.

## Release

- [ ] SDK dependency is pinned to a reviewed compatible release/commit.
- [ ] Package source/release is immutable and reviewable.
- [ ] Changelog/release notes describe user-visible changes.
- [ ] GamerHQ deployment pins the intended Skill release/commit.
- [ ] Rollback target is known before production rollout.

## Live acceptance after reviewed deployment

- [ ] Existing configuration is visible.
- [ ] Existing scheduled job survives restart.
- [ ] Create one harmless recurring post.
- [ ] Pause and resume it.
- [ ] Delete the test configuration.
- [ ] Disable/re-enable the Skill and confirm configuration remains.
- [ ] `/server manage → Skills` shows external package provenance.

Live acceptance is not a replacement for offline CI.
