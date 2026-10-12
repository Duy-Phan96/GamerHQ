# External GamerHQ Skill repository instructions

This repository contains one portable GamerHQ Skill.

Before editing implementation code, read the matching GamerHQ SDK version's:

- docs/skills/developer-guide.md
- docs/skills/authoring-contract.md
- docs/skills/review-checklist.md

## Hard rules

- Treat SkillManifest.id as permanent.
- Keep the `gamerhq.skills` entry-point name equal to the manifest ID.
- Use only public `skill_runtime` APIs for host access.
- Do not import Discord.py, GamerHQ bot/cogs/services/database/hosts/config, or another Skill's private code.
- Declare the minimum required capabilities.
- Use Skill Storage for private persistent data.
- Use the shared Scheduler for persisted future/recurring work.
- Keep `register` process-level and guild-independent.
- Keep `health_check` read-only.
- Disable must retain user data/configuration by default.
- Do not access tokens, `.env`, production databases, shell commands, runtime package installation or Git cloning.
- All tests must run without production access or a Discord token.

## Before coding

Write down:

1. Skill ID and purpose.
2. Required capabilities with rationale.
3. Storage schema and versioning.
4. Scheduler jobs/handler IDs.
5. Events and Public APIs.
6. Configuration flow.
7. Failure/retry/idempotency model.
8. Security/privacy considerations.
9. Test plan.

Do not implement a missing host feature by bypassing the SDK. Request a new
capability/contract instead.

## Repository ownership and handoffs

This Skill repository owns only this Skill.

It may inspect published GamerHQ Runtime/SDK contracts, but it must not modify the
GamerHQ Host repository, gamerhq-web or another Skill repository.

If a required Host capability/contract is missing:

1. finish all work possible with the current SDK;
2. document the blocker;
3. produce a handoff prompt for the GamerHQ repository containing the requested
   public outcome and acceptance criteria;
4. wait for GamerHQ to publish the required contract/version;
5. update this Skill to consume that released contract.

The Skill publishes and versions independently. GamerHQ may later choose to pin a
reviewed immutable Skill release, but this Skill does not manage GamerHQ server
releases or deployment.
