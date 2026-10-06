# Platform Neutrality & GamerHQ Reference Customer Model

## Core principle

GamerHQ is the first real consumer of the architecture, not the architecture itself.

The long-term model is:

```text
Neutral Platform
├── Runtime / SDK
├── Host contracts
├── Web dashboard
├── Marketplace
├── Developer tooling
└── Skill ecosystem
        ↑
        │
   GamerHQ
   first reference customer
```

GamerHQ is the first production-like integration and may be the default gaming
template, but the platform must remain usable by unrelated Discord communities.

## GamerHQ role

GamerHQ should be treated as:

- a real gaming community;
- the first reference customer;
- the primary integration environment while the platform matures;
- a useful default gaming template/distribution;
- optionally an early support/community hub for the platform.

GamerHQ should not become a hard dependency for:

- the Skill Runtime;
- the SDK;
- the web dashboard;
- the Marketplace;
- external Skills;
- generic server management contracts.

## Platform neutrality rule

A portable platform component must not assume:

- GamerHQ channel names;
- GamerHQ categories;
- GamerHQ-specific roles;
- GamerHQ-specific database tables;
- GamerHQ commands;
- GamerHQ business rules.

Instead, GamerHQ-specific behavior belongs in:

- GamerHQ host adapters;
- GamerHQ server configuration;
- GamerHQ-specific Skills;
- a future GamerHQ gaming template.

## Extraction direction

Features currently implemented directly in GamerHQ should periodically be
reviewed for extraction.

The key question is:

> Is this feature truly GamerHQ-specific, or is it a reusable platform Skill or
> host capability?

Likely extraction candidates include:

- Progression;
- Recurring Posts;
- Gaming News;
- Support Tickets;
- Giveaways;
- Polls;
- temporary voice rooms;
- member profiles;
- LFG;
- Events.

Some of these may remain first-party Skills maintained by the platform team,
while GamerHQ simply installs and configures them.

## Desired future GamerHQ shape

The long-term GamerHQ server should increasingly look like:

```text
GamerHQ
├── gaming-community configuration
├── installed first-party Skills
├── selected third-party/community Skills
└── minimal truly GamerHQ-specific glue
```

This keeps the real community useful while continuously testing platform
portability.

## Default template concept

A future new-server onboarding flow may offer templates such as:

```text
Choose a server setup:

○ Empty
● GamerHQ Gaming
○ Creator Community
○ Competitive Clan
```

The GamerHQ Gaming template could recommend/install:

- Member Profiles;
- Games;
- LFG;
- Events;
- Progression;
- Recurring Posts;
- Support;
- Temporary Voice.

Templates should compose reusable Skills and configuration. They must not create
a separate hard-coded platform fork.

## Support server decision

Whether GamerHQ also becomes the long-term platform support/community server is
intentionally unresolved.

Two valid futures are:

### Shared early community

```text
GamerHQ
├── Gaming Community
└── Platform / Developer Support
```

This may be efficient while the platform is small.

### Separate mature support community

```text
Platform
├── GamerHQ Gaming Community
└── Dedicated Platform Support Server
```

Do not couple the architecture to either outcome yet.

## Reference customer principle

GamerHQ should be used as a demanding reference customer.

When a new platform capability is added:

1. define it generically;
2. implement it through public contracts;
3. use GamerHQ as the first real consumer;
4. verify usability in the live gaming-community context;
5. avoid adding GamerHQ-only assumptions back into the portable layer.

This turns GamerHQ into a useful product test bed without compromising platform
neutrality.

## Naming and future extraction

Names such as "GamerHQ Skill Runtime" are acceptable during early development,
but public contracts should remain host-neutral enough to be renamed or
extracted later without a rewrite.

The desired dependency direction remains:

```text
Portable Platform Runtime / SDK
        ↓
Host adapters
        ↓
GamerHQ integration
```

not:

```text
GamerHQ business logic
        ↓
portable platform
```

## Architectural success criterion

The platform is genuinely reusable when:

- GamerHQ runs as one consumer;
- another unrelated Discord server can use the same Runtime and Skills;
- gamerhq-web can manage both without GamerHQ-specific frontend code;
- portable Skills require only public Runtime/SDK contracts;
- GamerHQ-specific defaults are expressed as configuration/templates rather
  than runtime assumptions.
