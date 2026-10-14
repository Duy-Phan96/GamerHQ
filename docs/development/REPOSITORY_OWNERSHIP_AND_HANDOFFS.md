# Repository Ownership and Handoffs

GamerHQ is developed as a set of independent repositories.

The default mental model remains:

> **one repository = one autonomous product boundary / one clear responsibility**

Each repository owns its own code, roadmap, issues, pull requests, tests, versioning,
releases and production/deployment decisions.

Repositories cooperate through stable public contracts. Cross-repository work is
allowed when it is explicitly authorized and documented; it is never permission
to bypass the target repository's architecture, review or release process.

## 1. Default rule

A project may always:

- inspect another GamerHQ repository read-only;
- consume released packages and documented public contracts;
- prepare a handoff for work that belongs elsewhere.

By default, stop at the repository boundary and hand work off.

## 2. Authorized cross-repository work

The owner may explicitly authorize one project/chat/agent to complete work in a
second repository.

When that happens:

1. inspect the target repository's `AGENTS.md`, SDLC and relevant architecture docs;
2. create a dedicated branch in the target repository;
3. keep the target change narrowly scoped to the approved requirement;
4. use the target repository's tests and CI;
5. open a target-repository PR with architecture/security/migration/deployment impact;
6. document where the request came from and which source contract/version it consumes;
7. report the target PR/commit back to the source repository;
8. never deploy or promote production merely because the cross-repository PR is green.

Do not work directly on `main` or another protected/release branch unless the
target repository's own release process explicitly requires it.

## 3. Ownership still matters

Cross-repository permission does not merge ownership boundaries.

Examples:

- GamerHQ Host may add an adapter for a public Skill contract;
- a Skill project may update GamerHQ to consume a released Skill when explicitly authorized;
- gamerhq-web may add a Host API integration when explicitly authorized.

But:

- GamerHQ must not copy a Skill's private business logic into the Host;
- a Skill must not depend on GamerHQ private modules;
- one Skill must not import another Skill's private implementation;
- a project must not silently change another repository without a recorded reason.

## 4. Handoffs remain the normal communication mechanism

Use a handoff when:

- authorization for cross-repository work is absent;
- the target repository needs to make an architectural decision;
- the source project only needs a public outcome, not a specific implementation;
- production/release ownership must remain with the target project.

A useful handoff contains:

```text
HANDOFF REQUIRED

FROM REPOSITORY:
<source>

TARGET REPOSITORY:
<target>

PROBLEM:
...

REQUESTED OUTCOME:
...

CURRENT PUBLIC CONTRACT / VERSION:
...

ACCEPTANCE CRITERIA:
- ...

COMPATIBILITY / MIGRATION:
- ...

SECURITY / CAPABILITY IMPACT:
- ...

NON-GOALS:
- ...

SOURCE PROJECT STATUS:
...

WHEN COMPLETE:
<version / commit / API the source should consume>
```

## 5. Cross-repository completion record

When authorized cross-repository work is performed, the target PR should state:

- source repository / handoff;
- public contract/version consumed;
- files/layers changed;
- tests/CI;
- compatibility/migration impact;
- security/capability impact;
- release/deployment impact.

The source repository should retain a concise completion reference to the target
PR/commit/version when useful.

## 6. Independent releases

Every repository publishes independently.

A green Skill release does not deploy GamerHQ. A green GamerHQ adapter does not
publish the Skill. A web release does not imply a Host release.

GamerHQ Host additionally owns deployment of the GamerHQ Discord server.

## 7. Dependency direction

Preferred direction:

```text
public Runtime / SDK contract
        ↓
independent external Skill
        ↓
released immutable Skill version
        ↓
GamerHQ Host adapter / reviewed immutable pin
```

Skill-to-Skill communication also uses public contracts/events only.

## 8. GamerHQ as reference customer

GamerHQ is the first/reference customer of the Skill Platform, not the platform
boundary.

The Host should focus increasingly on:

- Discord connection/Gateway observation;
- authorization/capabilities;
- Skill loading/lifecycle;
- Skill storage and scheduler primitives;
- public Runtime/SDK contracts;
- safe Discord adapters;
- generic management/API surfaces;
- diagnostics and deployment.

Stable domain behavior should live in independent Skills.

## 9. Release and production responsibility

Cross-repository authorization never authorizes production deployment.

Each target repository follows its own release workflow. GamerHQ production
updates remain explicit owner operations through the GamerHQ release/deployment
process.

## 10. Completion report

For meaningful cross-repository work report:

- source repository;
- target repository;
- target branch;
- target PR;
- target commit/version;
- tests/CI;
- public contract used/changed;
- storage/migration impact;
- security/capability impact;
- release/deployment impact;
- any remaining handoffs.
