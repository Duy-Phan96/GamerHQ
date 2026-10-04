# Extract Recurring Posts to its own GitHub repository

`packages/gamerhq-skill-recurring-posts/` is intentionally a complete repository-shaped package.

Moving it into a separate repository must be a packaging/deployment change, not a Skill rewrite.

## Target repository

Recommended repository name:

`gamerhq-skill-recurring-posts`

Repository root after extraction:

```text
gamerhq-skill-recurring-posts/
├─ .github/workflows/tests.yml
├─ AGENTS.md
├─ README.md
├─ SKILL_DESIGN.md
├─ pyproject.toml
├─ gamerhq_skill_recurring_posts/
│  ├─ __init__.py
│  └─ skill.py
└─ tests/
   ├─ test_recurring_posts_contract.py
   └─ test_skill.py
```

## Identity that MUST NOT change

- Skill ID: `recurring-posts`
- distribution: `gamerhq-skill-recurring-posts`
- Runtime API: `1`
- storage key: `posts.v1`
- scheduler handler: `recurring-post.execute.v1`
- job key pattern: `post:<post-id>`
- event: `recurring-post.sent.v1`
- management API IDs:
  - `recurring-posts.list.v1`
  - `recurring-posts.get.v1`
  - `recurring-posts.create.v1`
  - `recurring-posts.set-active.v1`
  - `recurring-posts.delete.v1`

Changing the Git repository must not create a new runtime identity or new persisted namespace.

## Extraction steps

1. Create the new repository.
2. Copy the contents of `packages/gamerhq-skill-recurring-posts/` to its repository root.
3. Keep the `gamerhq.skills` entry point unchanged.
4. Keep `[tool.gamerhq]` metadata aligned with `SkillManifest`.
5. Run the package's Python 3.12/3.14 CI without Discord credentials.
6. Pin a reviewed `gamerhq-skill-sdk` version/commit.
7. Produce a reviewed package version or immutable Git commit.
8. Change GamerHQ deployment to install that pinned external source instead of the bundled local directory.
9. Keep `recurring-posts` in the GamerHQ configured/bundled Skill IDs during the deployment transition.
10. Verify `/server manage → Skills` shows the same Skill ID and existing configuration.
11. Verify one existing persisted scheduler job survives restart.
12. Only then remove the bundled package directory from GamerHQ.

## Deployment rule

GamerHQ must install a pinned package version or immutable Git commit during image build/deployment.

Do not make Discord execute `git clone`, `pip install`, or arbitrary repository URLs at runtime.

Conceptual future dependency:

```text
GamerHQ deployment
      ↓
pinned gamerhq-skill-recurring-posts release/commit
      ↓
Python package installation
      ↓
gamerhq.skills entry point
      ↓
SkillRegistry / capability validation
```

## Migration acceptance

Before removing the bundled copy, verify:

- package CI passes on Python 3.12 and 3.14;
- GamerHQ discovers exactly one `recurring-posts` entry point;
- package provenance shows `gamerhq-skill-recurring-posts`;
- existing `posts.v1` data is visible;
- existing `skill_jobs` entries retain the same Skill ID/job keys;
- pause/resume/create/delete work through management contracts;
- restart recovery works;
- disabling the Skill retains configuration/jobs;
- no GamerHQ module imports the package's private implementation.

## Rollback

If the external package cannot load, GamerHQ should show the configured Skill as `Unavailable` while the rest of the bot continues.

Rollback by restoring the previously pinned package source/version. Do not rewrite or clear Skill storage merely to switch package source.
