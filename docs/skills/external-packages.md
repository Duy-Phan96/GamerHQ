# External Skill packages

GamerHQ Skills may live in independent Git repositories and Python packages.

The host does not clone or install remote code at runtime. Deployment installs a
reviewed package first; GamerHQ then loads only explicitly allowlisted Skill
entry points.

## Package contract

An external package exposes exactly one zero-argument factory through the Python
entry-point group:

```text
gamerhq.skills
```

Example `pyproject.toml`:

```toml
[project]
name = "gamerhq-skill-hello-world"
version = "0.1.0"
requires-python = ">=3.12"

[project.entry-points."gamerhq.skills"]
hello-world = "gamerhq_skill_hello_world:create_skill"
```

The entry-point name must exactly match `skill.manifest.id`.

Example factory:

```python
def create_skill():
    return HelloWorldSkill()
```

## Host allowlist

Installing a Python package is not sufficient to activate it.

The deployment must also explicitly list its Skill ID:

```env
GAMERHQ_EXTERNAL_SKILLS=hello-world,server-reminders
```

At startup GamerHQ:

1. reads the explicit allowlist;
2. discovers already-installed `gamerhq.skills` entry points;
3. loads only matching IDs;
4. verifies entry-point name == manifest ID;
5. passes the Skill through the normal SkillRegistry validation;
6. applies the same capability and Runtime API compatibility checks as first-party Skills.

Unknown, duplicated or broken packages fail closed.

## GitHub workflow

A future developer can keep each Skill in its own repository:

```text
gamerhq-skill-recurring-posts/
gamerhq-skill-events/
gamerhq-skill-xp/
gamerhq-skill-amazon-deals/
```

Each repository owns its implementation, tests, README, changelog and package
version. GamerHQ owns the Runtime/SDK and host capability contracts.

This means a Skill can be developed, versioned and reviewed without adding its
private implementation to the GamerHQ repository.

## Installation model

V1 installation is deployment-controlled through `requirements-skills.lock`.
Each external Skill is pinned to an immutable reviewed package artifact or Git commit before the production image is built.

Do not implement a Discord command that runs `git clone`, `pip install` or
executes a GitHub URL directly. Package installation changes executable code and
belongs to a reviewed deployment boundary.

A later website/marketplace may automate that deployment workflow after package
signing, trust policy, compatibility checks and rollback behavior are defined.

## SDK dependency

The public Runtime Python package is still physically located in GamerHQ today.
The entry-point contract is designed so `skill_runtime/` can later be published
as a dedicated SDK package without changing Skill manifests or entry-point IDs.

For a fully independent third-party developer experience, extracting/publishing
that SDK is the next packaging milestone.

See [Creating a Skill](creating-a-skill.md) and the
[external Skill template](../../examples/external-skill-template/README.md).


## Runtime provenance

When GamerHQ registers a Skill, the host records whether it came from:

- a built-in GamerHQ Skill; or
- an external installed Python distribution.

The Skills management detail view surfaces this provenance to administrators.

For external Skills the displayed source is the installed distribution name, not
an arbitrary URL supplied by the Skill.

This does not replace package signing or a future trust registry, but it prevents
the basic operational ambiguity of seeing an enabled Skill without knowing which
installed package provided it.

## Failure isolation

Configured external packages are loaded independently.

A missing, broken or incompatible external Skill must not prevent GamerHQ,
built-in Skills or another healthy external Skill from starting.

When loading fails:

- the package code is not activated;
- the Skill is marked unavailable in the host Runtime;
- `/server manage → Skills` still shows the configured Skill;
- no Enable action is offered while the package is unavailable;
- user-facing status uses a bounded generic explanation;
- private import/validation exception details are not exposed.

If a later deployment installs or fixes the package, successful registration
clears the unavailable marker and the normal disabled/enabled lifecycle resumes.


## External Skill lock file

GamerHQ tracks deployment-approved external Skills in:

`requirements-skills.lock`

Example:

```text
gamerhq-skill-recurring-posts @ https://github.com/Duy-Phan96/gamerhq-skill-recurring-posts/archive/<commit>.zip
```

The lock file is part of the GamerHQ release review. Updating a Skill therefore
requires a normal GamerHQ PR/CI/build cycle even if the Skill repository has
already merged a newer version.

This deliberately prevents a moving `main` branch in a Skill repository from
changing production behavior without host-side review.


## Reviewed deployment plan

`requirements-skills.lock` remains the single reviewed source of truth for
external Skill package dependencies.

GamerHQ can project that lock into a machine-readable deployment plan without
installing or executing anything:

```bash
python -m tools.skill_package_plan --json
```

The V1 plan reports:

- schema version;
- immutable package source repository;
- reviewed full commit SHA;
- distribution name;
- that an image rebuild/redeploy is required;
- that runtime package installation is not allowed.

The parser fails closed when a lock entry uses a moving branch, short commit,
unsupported source form, duplicate distribution or malformed requirement.

This provides a stable input for future CI/deployment orchestration while
preserving the current reviewed Docker build boundary.
