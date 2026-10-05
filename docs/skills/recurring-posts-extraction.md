# Recurring Posts external repository extraction

Status: **completed**

Recurring Posts now lives in its own repository:

`Duy-Phan96/gamerhq-skill-recurring-posts`

GamerHQ no longer carries the private Skill implementation under `packages/`.
The production/development dependency is pinned through
`requirements-skills.lock` to an immutable reviewed commit.

Current reviewed source commit:

`8317d854f0a39811804b9f28eff9b7061a5417e8`

## Preserved runtime identity

The repository move does not change:

- Skill ID: `recurring-posts`
- distribution: `gamerhq-skill-recurring-posts`
- Runtime API: `1`
- storage key: `posts.v1`
- scheduler handler: `recurring-post.execute.v1`
- job key pattern: `post:<post-id>`
- event: `recurring-post.sent.v1`
- management APIs:
  - `recurring-posts.list.v1`
  - `recurring-posts.get.v1`
  - `recurring-posts.create.v1`
  - `recurring-posts.describe.v1`
  - `recurring-posts.validate.v1`
  - `recurring-posts.update.v1`
  - `recurring-posts.set-active.v1`
  - `recurring-posts.delete-preview.v1`
  - `recurring-posts.delete.v1`

Existing guild enablement, Skill Storage and scheduler jobs therefore remain in
the same namespaces.

## Ongoing developer workflow

Changes to Recurring Posts are developed and reviewed in the separate Skill
repository.

A normal release flow is:

```text
Skill feature branch
      ↓
Skill repository PR
      ↓
Python 3.12 / 3.14 Skill CI
      ↓
merge to Skill repository main
      ↓
review immutable Skill commit
      ↓
GamerHQ updates requirements-skills.lock
      ↓
GamerHQ PR + full host CI + production image build
      ↓
GamerHQ main / owner-controlled VPS update
```

GamerHQ must never install the moving Skill `main` branch directly.

## Deployment rule

External code installation remains a build/deployment concern.

Discord interactions must never run `git clone`, `pip install`, or execute an
arbitrary GitHub URL.

If the pinned package cannot load, GamerHQ keeps the Skill configured but marks
it `Unavailable` and continues starting healthy Skills.

## Upgrade acceptance

When updating the pinned Recurring Posts commit, verify:

- standalone Skill CI passed on Python 3.12 and 3.14;
- static package capability metadata matches the manifest;
- GamerHQ discovers exactly one `recurring-posts` entry point;
- existing `posts.v1` configuration remains readable;
- existing scheduler jobs retain their keys and handler compatibility;
- management create/edit/preview/pause/resume/delete still works;
- disable/re-enable remains non-destructive;
- production Docker build installs the exact locked source;
- rollback is possible by restoring the previous lock-file commit.
