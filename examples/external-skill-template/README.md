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
The SDK is currently in the GamerHQ repository and is intended to become its own
installable package later.
