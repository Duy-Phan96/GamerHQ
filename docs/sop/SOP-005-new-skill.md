# SOP-005: New Skill

Use when a feature should become a portable first-party or third-party Skill.

Canonical sources:
- ../skills/developer-guide.md
- ../skills/authoring-contract.md
- ../skills/creating-a-skill.md
- ../skills/conformance.md
- ../skills/review-checklist.md
- ../skills/package-compatibility.md
- ../skills/governance.md

## Procedure

1. Architecture check: confirm the feature is reusable and should not remain GamerHQ-specific application logic.
2. Start from the external Skill template / an independent repository where appropriate.
3. Choose a stable lowercase Skill ID and semantic package version.
4. Declare Runtime API and SDK compatibility.
5. Define the minimum required capabilities.
6. Use only documented public Runtime/SDK contracts.
7. Define versioned events/public/management APIs when needed.
8. Define Management UI Schema for generic configuration when appropriate.
9. Keep Skill persistence in namespaced Skill Storage and use shared scheduler/events instead of private host subsystems.
10. Add package, manifest, capability, lifecycle, storage, scheduler and failure/retry tests as applicable.
11. Run conformance/independent-install checks.
12. Document configuration, storage schema, capabilities, APIs/events, migration and security behavior.
13. Release/version the Skill independently.
14. Complete Marketplace/publication governance review when the Skill will be listed.
15. Pin a reviewed immutable Skill artifact/commit in the host integration.

## Forbidden coupling

A portable Skill must not import GamerHQ bot, cogs, config, database, services,
hosts or Discord.py internals.

Do not add runtime Git clone/pip-install behavior to the Skill or Host as a
shortcut for Marketplace installation.
