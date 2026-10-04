# Skill SDK conformance checks

External Skill repositories can validate their public SDK shape without running
GamerHQ or connecting to Discord.

Example:

```python
from gamerhq_skill_example import create_skill
from skill_runtime import validate_skill_factory

report = validate_skill_factory(
    create_skill,
    expected_skill_id="example-skill",
)
print(report)
```

The validator checks:

- the factory is callable;
- factory construction succeeds without exposing private exception text;
- the returned object exposes a valid `SkillManifest`;
- the optional expected Skill ID matches the manifest ID;
- all required lifecycle methods exist;
- lifecycle methods are asynchronous.

It deliberately does not:

- execute lifecycle methods;
- access Discord;
- access storage or production state;
- inspect secrets;
- install dependencies;
- prove business logic correctness.

Use it as one fast preflight in addition to the full tests and
[Skill Review Checklist](review-checklist.md).


## Static source audit

Use the SDK source audit to catch obvious host coupling before review:

```python
from pathlib import Path
from skill_runtime import require_clean_skill_source

require_clean_skill_source((Path("gamerhq_skill_example"),))
```

The source audit does not import or execute the inspected files. It parses Python
source and rejects known forbidden boundaries such as:

- Discord.py imports;
- GamerHQ bot/cog/config/database/host/service imports;
- another first-party `skills` package import;
- `dotenv`;
- `subprocess`;
- direct `os.getenv` / `os.environ` access;
- `os.system` / `os.popen`.

This is a developer guard, not a malware sandbox or proof that code is safe.
Human review and the full Skill Review Checklist are still required.
