# GamerHQ Web Platform & Skill Marketplace

## Product vision

GamerHQ is intended to grow from a Discord bot into a user-based Skill Platform.

The web application is not merely a remote admin panel for one GamerHQ server.
It is the account and server management surface for users who sign in with
Discord, manage one or more Discord servers, install Skills from a marketplace,
and configure those Skills per server.

The long-term product model is:

```text
Discord Account
      ↓
GamerHQ User
      ↓
My Servers
      ↓
Selected Server
      ↓
Installed Skills
      ↓
Enabled / Disabled Skills
      ↓
Per-server Skill Configuration
```

A user may manage multiple Discord servers. Skill installation, enablement and
configuration therefore belong to a server/guild, not directly to the user.

## Core identity model

### User

A GamerHQ user authenticates with a Discord account.

The account is the entry point for:

- session identity;
- accessible Discord servers;
- Marketplace browsing;
- Developer features in the future.

### Server / Guild

A user sees only Discord servers they are authorized to manage.

Each server has independent:

- installed Skills;
- Skill enablement;
- Skill configuration;
- runtime health;
- server settings;
- members, games and events where exposed by GamerHQ.

Conceptually:

```text
User
├── Server A
│   ├── Progression
│   ├── Recurring Posts
│   └── Gaming News
│
└── Server B
    ├── Progression
    └── Music Quiz
```

The authoritative Skill configuration namespace remains:

```text
guild_id + skill_id
```

## Web dashboard

After Discord login, the user lands on a user dashboard.

Conceptual navigation:

```text
GamerHQ

Overview
My Servers
Marketplace
Account
Developer
```

Selecting a server opens a server-scoped dashboard:

```text
Server Dashboard

Overview
Members
Games
Events

Skills
├── My Skills
└── Marketplace

Server Settings
Logs
```

The exact navigation may evolve, but user identity and server scope must remain
explicit at all times.

## Skill states

Marketplace availability, installation, enablement and configuration are
different concepts and must not be collapsed into one boolean.

A Skill may be:

### Available

The Skill exists in the GamerHQ Marketplace.

### Installed

The server has added the Skill.

### Enabled

The Runtime is allowed to execute the Skill for that guild.

### Configured

The Skill has completed any required setup for that guild.

### Healthy

The Runtime reports that required capabilities and current configuration are
valid.

Example:

```text
Progression & Achievements

Installed:   Yes
Enabled:     Yes
Configured:  Yes
Health:      Healthy
Version:     1.2.0
```

## My Skills

The server dashboard should expose installed Skills separately from the
Marketplace.

Conceptual example:

```text
My Skills

🏆 Progression & Achievements
Enabled · Healthy
[ Configure ] [ Disable ]

🔁 Recurring Posts
Enabled · Healthy
[ Configure ] [ Disable ]

🎵 Music Quiz
Disabled
[ Enable ] [ Remove ]
```

Enable/disable must use the Runtime's existing per-guild Skill state rather than
creating a web-only enablement store.

## Skill Marketplace

The Marketplace describes Skills available to install.

Suggested categories:

- Featured;
- Official GamerHQ;
- Community.

A Marketplace card may show:

- name;
- icon;
- short description;
- publisher;
- version;
- pricing tier when relevant in the future;
- compatibility;
- install action.

A Skill detail page should show:

- full description;
- features;
- publisher;
- version;
- Runtime API compatibility;
- required capabilities;
- install action;
- documentation;
- later ratings/reviews if desired.

## Installation flow

Adding a Skill must not immediately perform hidden mutations.

Conceptual flow:

```text
Marketplace
→ Skill Detail
→ Choose Server
→ Review Capabilities
→ Add to Server
→ Configure
→ Enable
```

Only servers the authenticated Discord user is authorized to manage may be
offered as installation targets.

Before installation, the user should see the Skill's declared capabilities.

Example:

```text
Progression & Achievements requests:

✓ Skill storage
✓ Read members
✓ Read channels
✓ Send messages
✓ Manage safe reward roles
✓ Write audit records
```

The interface should also clearly communicate meaningful boundaries where
possible, for example that a safe reward role capability does not permit the
Skill to grant administrator roles.

## Marketplace security model

Community Skills must never provide arbitrary JavaScript that GamerHQ executes
inside the main dashboard.

The preferred model is:

```text
Skill
→ declarative Management UI schema
→ GamerHQ Web renderer
```

not:

```text
Skill
→ arbitrary React/JavaScript bundle
→ execute inside GamerHQ
```

This keeps:

- design consistent;
- mobile behavior consistent;
- dark/light themes consistent;
- validation controlled by GamerHQ;
- third-party code isolated from the dashboard;
- the Marketplace safer.

A future advanced custom UI model would require a separate sandbox/security
design and is not part of V1.

## Schema-driven Skill configuration

A portable Skill should eventually be able to describe its management UI
declaratively.

Conceptual contract:

```text
SkillManagementSchema
├── sections
├── fields
├── field types
├── validation
├── allowed values
├── help text
└── management operations
```

Potential V1 field types:

- boolean;
- integer;
- string;
- long_text;
- select;
- multi_select;
- discord_channel;
- discord_role;
- timezone;
- schedule.

Example conceptual field:

```json
{
  "key": "dailyCap",
  "type": "integer",
  "label": "Daily XP cap",
  "min": 0,
  "max": 10000
}
```

The website decides how that field is rendered.

## One configuration model, multiple clients

Discord UI and the web dashboard must not become separate sources of truth.

The intended relationship is:

```text
                 Skill Configuration
                         ↑
              versioned Management APIs
                ↑                   ↑
        Discord Admin UI       Web Admin UI
```

Both clients use the same Skill configuration and revision semantics.

For example:

```text
Discord opens revision 5
Website saves revision 6
Discord submits revision 5
→ stale save rejected
→ client reloads revision 6
```

This rule applies not only to Progression but to every configurable Skill.

## Progression as the first reference Skill

Progression & Achievements is the first strong test case for the web model.

Its web configuration should eventually expose:

### XP Sources

Checkbox/toggle and numeric configuration for:

- Voice XP;
- Chat XP;
- LFG participation;
- Event hosting;
- Community events;
- Tournament participation;
- Tournament wins;
- future Skill-provided sources.

Example:

```text
☑ Voice Activity

XP per interval      [ 5 ]
Minutes per interval [ 10 ]
Daily cap            [ 180 ]
```

### Level Curve

Editable:

- base XP;
- linear growth;
- quadratic growth;
- maximum level.

The web UI may also show a visual preview or graph.

### Achievements

Card/list management with:

- enabled state;
- name;
- emoji;
- description;
- condition;
- XP reward;
- edit action;
- create action.

The default booster achievement is named:

```text
💎 Server Booster
```

### Rewards

Guided reward builder:

```text
Trigger
→ Requirement
→ Reward Type(s)
→ Preview
→ Confirm
```

Supported/desired reward types include:

- badge;
- profile title;
- XP bonus;
- Discord role;
- channel access through a safe reward role;
- announcement.

One trigger may grant multiple rewards.

### Announcements

Configurable:

- enabled / disabled;
- destination channel;
- level-up announcements;
- achievement announcements;
- reward announcements;
- editable message template;
- preview.

Template variables may include:

- `{member}`;
- `{member_id}`;
- `{level}`;
- `{xp}`;
- `{total_xp}`;
- `{achievement_name}`;
- `{achievement_emoji}`;
- `{reward_name}`.

## Generic Skill integration goal

The web platform is successful only if a newly installed Skill can appear in
the dashboard without adding a custom frontend page for every Skill.

Target flow:

```text
Install external Skill package
        ↓
Host discovers Skill
        ↓
Runtime validates manifest
        ↓
Marketplace / My Skills knows the Skill
        ↓
Management schema is available
        ↓
Website renders configuration automatically
        ↓
Save uses versioned Management API
        ↓
Runtime applies server-scoped configuration
```

Progression and Recurring Posts should be used as the first two reference Skills
to prove that this generic flow works for meaningfully different products.

## External Skill repositories

After the platform contracts are stable, new Skills should normally be developed
as independent repositories.

Conceptual repository model:

```text
GamerHQ
→ host / platform integration

gamerhq-web
→ user dashboard and Marketplace

gamerhq-skill-recurring-posts
→ independent Skill

gamerhq-skill-progression
→ independent Skill

gamerhq-skill-<future>
→ independent Skill
```

Portable Skills depend only on public GamerHQ Skill Runtime / SDK contracts.

They must not import:

- GamerHQ cogs;
- GamerHQ database helpers;
- GamerHQ private services;
- Discord.py directly;
- another Skill's private implementation.

## Developer experience

A future authenticated user may also become a Skill developer.

Conceptual dashboard:

```text
Developer
→ My Skills
→ Create Skill
→ Validate
→ Test
→ Submit to Marketplace
```

Potential automated publication checks:

- valid manifest;
- supported Runtime API version;
- valid Management UI schema;
- declared capabilities;
- package compatibility;
- conformance tests;
- no forbidden GamerHQ-internal imports;
- required CI status;
- future package signing/provenance.

Marketplace publication and package installation remain separate concerns.

## Product roadmap

Recommended order after the current Progression work:

1. finish Progression Discord administration;
2. define Skill Management UI Schema V1;
3. create a separate `gamerhq-web` prototype;
4. add Discord login and user session;
5. implement My Servers;
6. implement server-scoped My Skills;
7. implement Marketplace prototype;
8. render Progression configuration from the generic schema;
9. render Recurring Posts configuration from the same generic system;
10. prove install / enable / disable / configure lifecycle end to end;
11. then build and extract future Skills as separate repositories.

The first web prototype should prioritize architecture proof over visual polish.

The key proof is:

```text
User signs in
→ selects a server
→ finds a Skill in Marketplace
→ adds it
→ enables it
→ generated configuration UI appears
→ saves configuration
→ Skill behavior changes on Discord
```

When that flow works for more than one unrelated Skill without custom frontend
code, GamerHQ has validated the core Skill Platform architecture.


## Marketplace governance

Marketplace discovery and executable package deployment are deliberately
separate.

A catalog entry may be Draft, Review Candidate, Approved, Published, Deprecated
or Blocked according to the [Skill Ecosystem Governance](governance.md).

Approval applies to a specific immutable Skill release.

Publishing a catalog entry does not authorize the GamerHQ Host to clone, install
or execute arbitrary remote code. Host package deployment remains a reviewed
deployment concern.

The current Add-to-Server flow therefore operates only on Skill packages already
available in the host deployment.

A future automated package-deployment service requires a separate trust,
provenance/signing, compatibility and rollback design.
