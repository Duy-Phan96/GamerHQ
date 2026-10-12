# Repository Ownership and Handoffs

GamerHQ is developed as a set of independent repositories.

The default mental model is:

> **one repository = one autonomous worker / bounded responsibility**

Each repository owns its own code, roadmap, issues, pull requests, tests,
versioning and releases.

Repositories cooperate through stable public contracts and explicit handoffs.
They do not silently edit each other.

## 1. Repository ownership is strict by default

A project working in one repository may:

- inspect another public GamerHQ repository when needed to understand a published
  contract or compatible version;
- consume released packages, documented APIs and immutable dependencies;
- propose a change required in another repository;
- prepare a handoff prompt for the owner/developer of that repository.

It must not directly modify another repository as part of its own task.

Examples:

- a Skill must not edit GamerHQ Host/Runtime/SDK code;
- gamerhq-web must not add Host APIs itself;
- GamerHQ Host must not implement a Skill's private feature code;
- one external Skill must not edit another external Skill.

If work is required elsewhere, stop at the repository boundary and hand it off.

The owner may explicitly start a separate task in the target repository. That
target project then decides how to implement the request within its own
architecture.

## 2. Handoffs are the communication mechanism

A handoff should be concise enough to paste into the target project's chat or
issue.

Use this structure when useful:

```text
HANDOFF REQUIRED

FROM REPOSITORY:
<source repo>

TARGET REPOSITORY:
<target repo>

PROBLEM:
<what cannot be completed with the current public contract>

WHY THIS BELONGS TO THE TARGET:
<ownership/boundary explanation>

REQUESTED OUTCOME:
<behavior or public contract needed>

CURRENT PUBLIC CONTRACT:
<relevant API/runtime/SDK/version>

ACCEPTANCE CRITERIA:
- ...
- ...

COMPATIBILITY / MIGRATION CONSTRAINTS:
- ...

NON-GOALS:
- ...

SOURCE WORK BLOCKED OR OPTIONAL:
<what can continue independently>

WHEN COMPLETE:
<published version/commit/API that the source repo can consume>
```

The handoff describes the need, not a command to copy a particular internal
implementation.

The receiving repository remains responsible for its architecture.

## 3. Independent releases

Every repository publishes independently.

Examples:

- an external Skill can release a new Skill version even when GamerHQ Host does
  not update immediately;
- gamerhq-web can release independently when its consumed Host API is already
  available;
- GamerHQ Host can release without forcing unrelated Skills to release.

A repository reports its own state using language such as:

- ready for review;
- ready for release;
- released as version X;
- compatible with Runtime API Y;
- handoff required from repository Z.

There is no global release that all GamerHQ repositories must join.

When GamerHQ Host wants to consume a new Skill release, GamerHQ updates its own
reviewed immutable pin in its own repository.

## 4. Dependency direction

Preferred dependency flow:

```text
public Runtime / SDK contract
        ↓
independent external Skill
        ↓
released immutable Skill version
        ↓
GamerHQ may choose to pin/install it
```

For web:

```text
public Host API
        ↓
gamerhq-web
```

If the required upstream contract does not exist, create a handoff instead of
changing the upstream repository directly.

## 5. GamerHQ is a customer, not the platform boundary

GamerHQ is the first/reference customer of the Skill Platform and may provide a
default/reference configuration.

Long term, the GamerHQ application should become progressively thinner.

Target direction:

```text
Discord / GamerHQ server
        ↓
Host adapters + server observation
        ↓
Skill Runtime / SDK / capability boundary
        ↓
independent installed Skills
```

GamerHQ-specific functionality should be extracted gradually when it has a
stable contract and clear ownership.

The core Host should increasingly focus on platform primitives such as:

- Discord connection and Gateway observation;
- server/resource inspection needed by public Host capabilities;
- authorization and capability enforcement;
- Skill loading, enablement and lifecycle;
- Skill storage and scheduler primitives;
- public Runtime/SDK contracts;
- safe Discord adapters;
- host diagnostics and deployment;
- generic management/API surfaces.

Domain/product behavior should move to Skills when mature, for example:

- recurring posts;
- progression / XP / achievements;
- affiliate/provider integrations;
- polls;
- reminders;
- giveaways;
- creator integrations;
- other community features that do not need to be Host primitives.

Do not extract merely for aesthetics. Extract when the public contract, storage
identity, lifecycle and tests are stable enough for an independent repository.

## 6. Skills may communicate, but only through public contracts

Independence does not mean isolation.

A Skill may expose public events/APIs that another Skill can optionally consume.

Example:

```text
Events Skill
  emits: event.completed.v1
        ↓
Progression Skill
  optionally awards XP
```

The consumer must remain functional when the optional provider is absent unless
the dependency is explicitly declared.

Never import another Skill's private implementation.

## 7. Developer / third-party model

Treat an external Skill developer like a real third-party developer.

They can:

- use the public SDK;
- request new Host capabilities;
- publish a Skill independently;
- submit a proposal/handoff for a missing contract.

They cannot assume permission to change GamerHQ infrastructure, private Host
internals or another developer's repository.

This is the same boundary AI-assisted projects must follow.

## 8. Cross-repository inspection vs modification

Read-only inspection across repositories is allowed when it helps verify a
published dependency or contract.

Cross-repository modification is not.

If the task appears impossible without another repository change:

1. finish everything possible in the current repository;
2. document the exact blocker;
3. produce a handoff prompt;
4. wait for the target repository to publish the required contract/version;
5. then update the current repository to consume it.

## 9. Release and production responsibility stays local

Each repository owns its own release process.

GamerHQ Host additionally owns deployment of the GamerHQ Discord server because
that server runs the GamerHQ Host application.

That does not make GamerHQ the release manager for other repositories.

External Skills publish independently. GamerHQ chooses which reviewed immutable
versions to install.

The GamerHQ Server Release Snapshot is therefore a **GamerHQ-repository local
deployment record**, not a cross-project orchestration mechanism.

## 10. Required project completion report

When meaningful work finishes, report only what the current repository owns:

- repository;
- branch;
- PR;
- version/commit when released;
- tests/CI;
- public contract changes;
- storage/migration impact;
- compatibility/dependency requirements;
- production/deployment impact for this repository;
- handoffs required from another repository, if any.

Do not perform the handoff's implementation from the source project.
