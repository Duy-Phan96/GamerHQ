# Skills management

GamerHQ exposes Skill administration through:

```text
/server manage → Skills
```

The panel is a host UI over the portable Skill Runtime.

## Three different states

### Registered / available

The Skill code exists in the current application build and its manifest passed Runtime validation.

This does **not** mean it is enabled on a server.

### Enabled

The server owner has enabled the Skill for this guild.

Enablement is persisted in host-owned state.

### Running

The enabled Skill has successfully completed its current Runtime start lifecycle.

An enabled but non-running Skill is a health condition that should be reviewed rather than silently treated as healthy.

## Owner control

Administrators may inspect Skills and their manifests.

Only the current server owner may confirm enable/disable changes.

Enable/disable uses a separate confirmation step showing the Skill's requested capabilities before persistent state changes.

Disabling a Skill:

- stops its current runtime lifecycle;
- prevents future Event/API/Scheduler execution through availability gates;
- removes guild-scoped Event subscriptions;
- retains Skill data unless a separate explicit uninstall/delete flow is introduced later.

Disable is therefore not uninstall and not data deletion.

## Manifest inspection

The panel exposes:

- Skill name and version;
- Runtime API version;
- description;
- requested capabilities;
- Events emitted;
- Events consumed;
- Public Skill APIs exposed;
- Public Skill APIs consumed;
- current health state.

This makes dependency surfaces visible to server owners and future SDK developers.

## Restart recovery

At bot startup, GamerHQ:

1. initializes the portable Runtime host;
2. registers the first-party Skills included in the current build;
3. reads persisted enabled Skill IDs per guild;
4. starts available enabled Skills independently.

An unknown enabled Skill record is reported as unavailable and is not loaded by name or guessed from Discord state.

One Skill failing to start must not prevent other available Skills from being restored.

## Server Check

The read-only Server Check includes a **Skills** area.

It reports:

- number of registered Skills;
- number enabled;
- number running;
- unavailable enabled records;
- enabled Skills that are not running.

The health panel can route directly into Skills management.

## First-party registry

GamerHQ first-party Skills are exposed through the explicit `skills.first_party_skills()` registry.

Existing GamerHQ cogs/services are **not** automatically treated as Skills.

This keeps migration deliberate and reviewable.

See [Existing GamerHQ features → Skill migration map](migration-map.md).

## Future installation model

The current panel manages Skills already included in the application build.

A future website/marketplace may add a separate install flow:

```text
Discover
→ Review manifest
→ Review capabilities/resource scopes
→ Validate compatibility
→ Install package
→ Enable for guild
```

Package installation, code signing, sandboxing and third-party distribution are not implemented by the current management panel.
