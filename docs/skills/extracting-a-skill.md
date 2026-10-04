# Extracting a built-in Skill to its own repository

This guide describes how to move an existing built-in GamerHQ Skill into an
independent Git repository without changing its runtime identity or losing
guild configuration.

## Goal

Before:

```text
GamerHQ repository
└─ skills/my_skill.py
```

After:

```text
GamerHQ repository
└─ Skill Runtime + host adapters only

gamerhq-skill-my-skill repository
├─ pyproject.toml
├─ README.md
├─ AGENTS.md
├─ SKILL_DESIGN.md
├─ gamerhq_skill_my_skill/
└─ tests/
```

The Skill continues to use the same:

- `SkillManifest.id`;
- storage namespace;
- scheduler Skill ID/job keys;
- Event IDs;
- Public Skill API IDs;
- Management API IDs.

## 1. Freeze public identity

Do not rename the Skill during extraction.

Keep:

```python
manifest.id == "my-skill"
```

Changing the ID would create a different storage/scheduler namespace and would
require an explicit migration.

## 2. Verify portable boundaries first

Before moving code, the Skill must already:

- import only public `skill_runtime` APIs;
- have no Discord.py/GamerHQ DB/service/cog/host imports;
- use shared Skill Storage;
- use shared Scheduler jobs;
- expose host configuration through Management APIs;
- communicate with other Skills only through Events/Public APIs.

Run:

```python
require_clean_skill_source(...)
validate_skill_factory(...)
```

## 3. Create the external package

Start from `examples/external-skill-template/`.

Add the stable entry point:

```toml
[project.entry-points."gamerhq.skills"]
my-skill = "gamerhq_skill_my_skill:create_skill"
```

Add package metadata:

```toml
[tool.gamerhq]
skill-id = "my-skill"
runtime-api = "1"
sdk = ">=0.1,<0.2"
capabilities = []
```

The static capability list must exactly match `SkillManifest.permissions`.

## 4. Move implementation and tests

Move Skill-owned implementation, models and tests into the external repository.

Do not move GamerHQ host UI, DB adapters or Discord.py adapters into the Skill
repository.

If the Skill needs custom GamerHQ administration, keep the host renderer in
GamerHQ and communicate only through versioned Management APIs.

## 5. Preserve persisted state

GamerHQ Skill Storage is scoped by:

```text
guild_id + skill_id + storage_key
```

Scheduler jobs are scoped by:

```text
guild_id + skill_id + job_key
```

Therefore moving Python code into another repository does not itself require a
database migration as long as the Skill ID, storage keys and job keys remain
compatible.

## 6. Install package in the deployment image

The external package must be installed during the reviewed image build.

Do not install it from a Discord interaction or at runtime.

Pin a released package version or immutable Git commit.

## 7. Allowlist the Skill

The deployment must explicitly allow the installed Skill ID:

```env
GAMERHQ_EXTERNAL_SKILLS=my-skill
```

Installed does not mean trusted/enabled.

## 8. Remove built-in registration

Only after the external package path passes CI:

- remove the built-in implementation from `first_party_skills()`;
- remove duplicate source code from GamerHQ;
- keep host-side management rendering only when it talks through public
  Management APIs;
- ensure only one package provides the Skill ID.

Never leave both built-in and external registrations active.

## 9. Run isolated package verification

The external repository must pass:

- Python 3.12/3.14 CI;
- package metadata validation;
- source portability audit;
- factory/lifecycle conformance;
- Skill-specific offline tests.

GamerHQ integration CI must additionally prove:

- installed entry-point discovery;
- manifest/capability compatibility;
- management contract routing;
- scheduler restart behavior where relevant.

## 10. Deployment transition

Before the production update:

1. back up the production database;
2. build an image containing the external package;
3. verify the package/Skill provenance;
4. confirm the old built-in implementation is no longer registered;
5. run offline/preflight tests;
6. deploy through the normal reviewed `main` update process.

Existing guild enablement and Skill Storage remain authoritative.

## 11. Rollback

A rollback image must contain a compatible implementation for the same stable
Skill ID.

Do not change or delete stored data merely because a package was temporarily
removed. A missing external package should appear as unavailable and must not
erase its configuration.

## Recurring Posts reference extraction

Recurring Posts is the first intended real extraction.

Before moving it, GamerHQ must have no direct import of
`skills.recurring_posts`. The host configuration UI must use only these
versioned Management APIs:

- `recurring-posts.list.v1`
- `recurring-posts.get.v1`
- `recurring-posts.create.v1`
- `recurring-posts.set-active.v1`
- `recurring-posts.delete.v1`

Once that boundary is tested, the implementation can move to a separate
`gamerhq-skill-recurring-posts` repository without changing the host UI.
