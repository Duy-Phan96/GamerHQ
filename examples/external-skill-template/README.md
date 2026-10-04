# GamerHQ external Skill template

Copy this directory into a new Git repository when starting an independent Skill.

The important contract is the Python entry point:

```toml
[project.entry-points."gamerhq.skills"]
example-skill = "gamerhq_skill_example:create_skill"
```

The entry-point name and `SkillManifest.id` must match.

This template intentionally does not access Discord.py, GamerHQ database helpers,
services, cogs, tokens or another Skill's private implementation.

During development, test against the matching GamerHQ Skill Runtime SDK version.
The SDK is currently built from the GamerHQ repository as the
`gamerhq-skill-sdk` distribution.

For an independent repository, pin the exact reviewed SDK commit during
development, for example:

```sh
python -m pip install "gamerhq-skill-sdk @ git+https://github.com/Duy-Phan96/GamerHQ.git@<reviewed-commit>"
```

Do not depend on a moving branch for a released Skill. Pin a commit or later a
published SDK version.


## Before implementing

Read the canonical authoring documents in the GamerHQ SDK repository:

- `docs/skills/developer-guide.md`
- `docs/skills/authoring-contract.md`
- `docs/skills/review-checklist.md`

Your Skill should be reviewable against those rules without access to GamerHQ
production, secrets or a live Discord token.
