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


## Cross-project release boundary

This repository can become **READY FOR GAMERHQ INTEGRATION** after its own
package/conformance/CI gates are green.

It must not independently recommend a production GamerHQ server update.

For the GamerHQ ecosystem, deployment is decided only by one immutable GamerHQ
Host release candidate and its Server Release Snapshot. Report this Skill's:

- Skill ID;
- package/distribution version;
- Runtime API;
- capabilities;
- immutable reviewed commit/release;
- migration/storage impact;
- required Host integration/pin;
- whether it should be included in the next GamerHQ release snapshot.

The canonical ecosystem rule is maintained in the GamerHQ Host repository:

`docs/development/GAMERHQ_ECOSYSTEM_WORKING_RULES.md`

and the deployment decision template is:

`docs/development/SERVER_RELEASE_SNAPSHOT.md`

Never advise deploying a moving `develop` branch merely because this Skill's
own CI is green.
