# GamerHQ Engineering Vision

GamerHQ is more than a collection of Discord commands. The project is being developed as a clean, modular community platform that can grow beyond its current server implementation.

This document describes long-term engineering direction. It is intentionally separate from the current feature list: items here are architectural goals, not promises that every capability already exists.

## 1. Professional engineering quality

The repository should be understandable to an external engineer without private project context.

That means:

- clear module ownership and dependency direction;
- small, testable services instead of duplicated feature systems;
- documented architecture and design decisions;
- explicit security and permission boundaries;
- additive, reviewed database migrations;
- deterministic tests and regression coverage;
- observable runtime behavior and useful health diagnostics;
- no secrets, runtime databases, private transcripts or production-only artifacts in source control;
- consistent naming and user-facing copy;
- minimal legacy code left unexplained.

The repository should demonstrate how GamerHQ is designed, not merely that it works.

## 2. Modular Skill platform

GamerHQ is the first host of a portable Skill Runtime.

Long term, functionality such as Recurring Posts, XP, Events, LFG, temporary voice, affiliate integrations, game feeds, Twitch alerts, moderation, giveaways and tournaments may be implemented as Skills that share the same stable SDK contracts.

A Skill should depend on:

- host capabilities;
- documented Events;
- explicit versioned Public Skill APIs;
- namespaced Skill storage;
- shared scheduler contracts.

A Skill must not depend on another Skill's private implementation.

The portable Runtime should eventually be extractable into its own package or repository without rewriting GamerHQ.

## 3. Developer SDK

Future first-party and third-party developers should be able to build Skills against a documented SDK.

The SDK should eventually provide:

- manifest schemas;
- lifecycle interfaces;
- capability definitions;
- Event schemas and tooling;
- Public Skill API contracts;
- scheduler contracts;
- namespaced storage;
- test utilities and a fake host;
- validation tooling;
- compatibility/version checks;
- security guidance;
- example/reference Skills.

A developer should be able to test most Skill behavior without a live Discord bot token.

## 4. AI-ready architecture

AI may become a platform capability in the future, but should not be scattered across individual Skills as direct provider-specific integrations.

A future host capability may expose contracts such as:

```text
ai.generate-text.v1
ai.summarize.v1
ai.classify.v1
```

Skills would request the appropriate AI capability and use a documented contract. The host would remain responsible for provider selection, model configuration, limits, privacy policy, observability and secrets.

This makes it possible to change AI providers or move AI execution to a dedicated service without rewriting every Skill.

## 5. AI community assistant

A future GamerHQ deployment may expose an AI assistant through one or more Discord channels.

Possible use cases include:

- community questions;
- help navigating GamerHQ features;
- event or LFG assistance;
- support triage;
- moderation assistance;
- summaries and informational content.

This is future scope. Any implementation must preserve access controls, privacy boundaries, rate limits and clear distinction between automated output and authoritative server state.

## 6. AI-assisted Skill creation

A longer-term product direction is a web-based Skill Builder.

Conceptually:

```text
Developer / Server Owner
          ↓
      Skill Builder
          ↓
 AI-assisted generation
          ↓
 Manifest + SDK-conformant code
          ↓
 Validation + contract tests
          ↓
 Permission review
          ↓
 Skill package / installation
```

AI should not receive unrestricted authority to create arbitrary bot code with full access.

Generated Skills should still be constrained by:

- declared capabilities;
- manifest validation;
- SDK contracts;
- event/API schemas;
- storage isolation;
- automated tests;
- security review;
- installation permissions;
- future sandboxing/signing policy where appropriate.

The SDK therefore serves both human developers and future AI-assisted developer tooling.

## 7. Host independence

GamerHQ-specific business logic belongs in the GamerHQ host layer.

The portable Runtime and SDK must not require:

- GamerHQ channel names;
- GamerHQ database tables;
- GamerHQ commands;
- Discord.py objects;
- GamerHQ secrets;
- another Skill's internal code.

This keeps open future options such as:

```text
Portable Skill Runtime
├── GamerHQ Discord Host
├── another Discord community product
├── web/control-plane services
└── isolated Skill workers
```

These are architectural options, not current deployment topology.

## 8. Evolution strategy

The project should evolve in small, reviewable slices:

1. stabilize current GamerHQ behavior;
2. establish portable Skill contracts;
3. build Event and Public Skill API infrastructure;
4. add shared persistence and scheduling;
5. implement Recurring Posts as the first reference Skill;
6. expose a clean Skills management experience;
7. validate the SDK through additional first-party Skills;
8. extract the Runtime/SDK only when the boundaries have proven stable;
9. add AI capabilities when there is a concrete product need and appropriate security model.

The guiding principle is to keep today's implementation useful while preserving tomorrow's architectural options.
