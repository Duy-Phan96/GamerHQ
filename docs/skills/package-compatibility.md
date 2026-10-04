# External Skill package compatibility

Every external Skill distribution should declare its intended GamerHQ SDK
compatibility in `pyproject.toml`.

Example:

```toml
[project.entry-points."gamerhq.skills"]
example-skill = "gamerhq_skill_example:create_skill"

[tool.gamerhq]
skill-id = "example-skill"
runtime-api = "1"
sdk = ">=0.1,<0.2"
```

The SDK validates that:

- one Python distribution exposes exactly one GamerHQ Skill;
- `tool.gamerhq.skill-id` matches the `gamerhq.skills` entry-point name;
- Runtime API compatibility is explicitly documented;
- the intended SDK version range is explicitly documented;
- the entry point targets a `module:factory`.

The executable `SkillManifest` remains authoritative at Runtime load time.
The `tool.gamerhq` section is developer/package metadata used for review,
CI and future package tooling.

## Why keep both Runtime API and SDK compatibility?

They answer different questions.

`runtime-api = "1"` means:

> Which public behavioral contract does this Skill implement?

`sdk = ">=0.1,<0.2"` means:

> Which SDK package versions was this Skill authored and tested against?

A normal Skill patch/release does not require changing the Runtime API.

## Independent repository dry run

GamerHQ CI contains an isolation test that installs:

1. the `gamerhq-skill-sdk` distribution;
2. the external Skill template distribution;

into a clean temporary Python target.

A new Python process then discovers the Skill only through the installed
`gamerhq.skills` entry point and validates the returned Skill implementation.

This guards against accidentally relying on GamerHQ application modules merely
because the Skill template lives inside the same source repository today.
