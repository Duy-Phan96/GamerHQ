# GamerHQ Core Beta Acceptance

This checklist defines the product-level acceptance gate for the core GamerHQ
member journey. It complements the repository test suite and the broader
[release checklist](../../RELEASE_CHECKLIST.md); it does not replace either one.

The checklist is intentionally candidate-specific. A successful result applies
only to the exact Host version and commit tested. New code, new immutable Skill
pins or a re-cut release candidate require a new decision.

## Product journey under test

```text
member joins or returns
    -> completes or updates profile/onboarding
    -> selects games
    -> creates, discovers or joins an LFG/event
    -> uses the relevant voice flow
    -> can request support privately
    -> restart preserves state and creates no duplicates
```

This is the minimum product gate for calling the current GamerHQ community beta
coherent. It does not declare every roadmap item complete.

## Relationship to the release process

Use this checklist only after an exact release candidate exists.

```text
develop (-dev)
    -> frozen release/<version>-rcN candidate
    -> automated candidate gates
    -> this core-beta acceptance
    -> remaining release-specific acceptance
    -> accepted main/tag
    -> owner-run VPS deployment
```

A moving `develop` build is not a release candidate and must not receive a
production acceptance decision.

The authoritative operational references remain:

- [Server Release Snapshot](SERVER_RELEASE_SNAPSHOT.md)
- [Release workflow](../../RELEASE_WORKFLOW.md)
- [Release checklist](../../RELEASE_CHECKLIST.md)
- [Production rules](../../PRODUCTION_RULES.md)
- [Deployment](../../DEPLOY.md)
- [Rollback](../../ROLLBACK.md)

## Result states

Use exactly one final state:

- `NOT STARTED` — prerequisites or accounts are not ready;
- `IN PROGRESS` — execution has started and no blocker is known;
- `BLOCKED` — a required check cannot be completed safely;
- `REJECTED` — a material privacy, data, authorization or stability regression
  was found;
- `PASSED FOR THIS CANDIDATE` — every mandatory item passed for the exact
  recorded version and SHA.

Do not mark a required item `N/A` merely because it is inconvenient. `N/A` is
valid only for an explicitly optional configuration, such as an optional
role-gated game channel that the reviewed guild has intentionally not enabled.
Record the reason.

## Required test identities

Use separate current server members so authorization and privacy can be tested
rather than inferred:

- Owner or Administrator;
- Staff member A;
- Staff member B, where exclusive assignment or revocation is tested;
- Member A;
- Member B.

A single account switching roles is not sufficient for private visibility and
concurrent-assignment checks.

Use fictional or test content. Do not place passwords, payment data, tokens,
private invite codes or real support narratives into acceptance evidence.

## Preconditions

Record or verify all of the following before starting:

- [ ] exact Host version ends in `-rcN` or is the exact accepted `main` version;
- [ ] exact 40-character candidate SHA is recorded;
- [ ] candidate CI is green on Python 3.12 and 3.14;
- [ ] release metadata preflight passed for the candidate channel;
- [ ] reviewed external Skill pins are immutable and install successfully;
- [ ] production image/Compose validation passed;
- [ ] Server Release Snapshot identifies included and excluded work;
- [ ] a verified SQLite backup exists outside the candidate image;
- [ ] migration/preflight has run on a copy of the intended database;
- [ ] exactly one bot instance can connect to the target guild/database;
- [ ] local/PC and old test instances are stopped;
- [ ] rollback version/commit and database recovery path are known;
- [ ] environment is identified as test guild or explicitly approved production
      acceptance;
- [ ] no real `.env` values are copied into notes, comments or screenshots.

Recommended read-only candidate check:

```bash
python -m tools.release_readiness --expect-channel candidate
```

Use the existing production-preflight procedure with the reviewed database path
or a safe copy. Do not publish that private path as evidence.

## Immediate stop conditions

Stop the acceptance run and record a blocker or rejection if any of these occur:

- the bot points at the wrong guild, application or database;
- a second bot instance is active against the same guild/database;
- a private ticket, private event, Staff channel or private log becomes visible
  to an unrelated member;
- setup, health or startup performs an unexpected destructive action;
- an unexpected migration, integrity error or traceback appears;
- a restart creates duplicate managed channels, roles, messages or pins;
- the candidate cannot be rolled back safely;
- evidence would require exposing secrets or private user content.

`/server health` is read-only. Do not turn a failed health check into automatic
setup or repair. Record the finding first. Any reconcile/repair must use the
existing preview and confirmation flow, be explicitly reviewed by the owner and
be followed by a rerun of every affected acceptance section.

# Acceptance procedure

## 1. Candidate and baseline

Record:

- [ ] Host version;
- [ ] candidate SHA;
- [ ] release branch;
- [ ] immutable external Skill pins or reviewed lock SHA;
- [ ] environment type;
- [ ] date/time and owner executing the run;
- [ ] previous accepted production version/commit, if production is involved;
- [ ] confirmation that a verified backup exists;
- [ ] confirmation that one active bot instance is enforced.

Then:

- [ ] start the exact reviewed candidate without editing production state files;
- [ ] confirm startup completes without traceback;
- [ ] run `/server health` and inspect private Details;
- [ ] record all warnings/manual-review findings before any mutation;
- [ ] confirm the expected persistent database is in use;
- [ ] confirm no duplicate managed resources appeared on first startup.

## 2. Profile and onboarding

With Member A:

- [ ] open the supported Get Started/Profile flow;
- [ ] complete Gender/About You and Age choices using the current supported
      flow;
- [ ] review before saving;
- [ ] confirm the expected roles are assigned;
- [ ] confirm unrelated roles are preserved;
- [ ] reopen the profile flow and verify current selections are preselected;
- [ ] change one supported value, review and save;
- [ ] confirm only the intended managed role changes;
- [ ] cancel/back out of a second edit and confirm no state changes.

With Member B:

- [ ] complete an independent profile flow;
- [ ] confirm Member A and Member B state does not leak between sessions;
- [ ] confirm a normal member cannot use owner/admin onboarding management.

## 3. Games

With Member A:

- [ ] open Choose Your Games or `/game select`;
- [ ] confirm current selections are displayed correctly;
- [ ] add a game and confirm its role is applied once;
- [ ] repeat/refresh and confirm no duplicate or reversed action;
- [ ] remove the game and confirm the role is removed once;
- [ ] browse Popular and A-Z pages without losing the current state;
- [ ] confirm selecting a game does not enable unrelated notification roles;
- [ ] confirm a game role remains usable when no optional text channel exists.

When the reviewed guild intentionally has an optional role-gated game channel:

- [ ] selected member can view it;
- [ ] unrelated member cannot view it;
- [ ] removing the role removes access;
- [ ] unrelated channel permissions/history remain unchanged.

## 4. LFG and events

Public event:

- [ ] Member A opens the existing event builder from the managed LFG entry;
- [ ] Member A can also open the same supported builder through `/lfg create`;
- [ ] create a public event with safe test content;
- [ ] Member B can discover and join it;
- [ ] join/leave counters and participant state update correctly;
- [ ] Member A can edit/reschedule/manage participants;
- [ ] Member B cannot use host-only management;
- [ ] sharing returns a usable link without exposing private data;
- [ ] closing/cancelling reaches the expected final state without duplicate
      cards/resources.

Private event:

- [ ] create a private event;
- [ ] unrelated Member B cannot discover/view its private resources before
      invitation;
- [ ] invite/join-code flow grants only the intended participant access;
- [ ] removed/uninvited members lose private access as designed;
- [ ] private event history/state survives the planned restart.

Community Events:

- [ ] managed Community Events opens the existing event builder;
- [ ] it does not create a second event system or incompatible state.

## 5. Temporary voice

- [ ] Member A joins the configured Create Voice entry;
- [ ] exactly one owned temporary room is created;
- [ ] Member A can use the supported rename, limit and lock controls;
- [ ] unrelated Member B cannot manage Member A's room;
- [ ] intended music-bot access works without exposing private Staff/ticket
      areas;
- [ ] rapid/repeated generator activity does not create duplicate rooms;
- [ ] leaving the room empty triggers the supported cleanup behavior;
- [ ] an occupied room is not deleted prematurely;
- [ ] tracked ownership and cleanup behavior remain correct after restart.

## 6. Private support

Isolation:

- [ ] Member A creates a General Support private chat;
- [ ] Member B creates a separate General Support private chat;
- [ ] each member sees only their own ticket;
- [ ] neither member can see ticket logs or manage channel permissions;
- [ ] support entry retries/double-clicks do not create duplicate active tickets.

Staff lifecycle:

- [ ] Staff A opens `/tickets overview` and sees current active counts/status;
- [ ] the overview is ephemeral and contains no ticket description/private
      narrative;
- [ ] Staff A opens the existing private ticket from the overview;
- [ ] Staff A takes Member A's ticket;
- [ ] Staff B cannot silently replace an active assignee;
- [ ] Staff A marks waiting and resumes using existing ticket controls;
- [ ] overview refresh reflects the changed assignment/status;
- [ ] revoking Staff A's role immediately prevents further Staff-only overview
      or mutation access;
- [ ] restored/current Staff access behaves normally without widening member
      visibility.

Closure and history:

- [ ] creator cancels a close confirmation and ticket remains open;
- [ ] creator or authorized Staff confirms closure;
- [ ] closed history remains readable to the intended creator/Staff;
- [ ] posting is locked as designed;
- [ ] repeated close is harmless;
- [ ] overview no longer lists the closed ticket as active;
- [ ] restart preserves assignment/status/history and creates no new channel.

## 7. Restart and persistence

Before restart, record only non-sensitive identifiers/counts needed to detect
unexpected duplication. Do not publish private member/ticket contents.

- [ ] stop the candidate cleanly;
- [ ] confirm no second instance starts during the restart window;
- [ ] start the same candidate against the same reviewed database;
- [ ] startup completes without traceback or unexpected migration;
- [ ] profile/game roles remain correct;
- [ ] managed selector/entry messages retain their intended IDs;
- [ ] public/private event state remains correct;
- [ ] ticket assignment/status/history remains correct;
- [ ] temporary voice tracking/cleanup remains correct;
- [ ] no duplicate managed channel, role, message or pin appears;
- [ ] disabled Skills retain configuration and no external Skill state is
      silently replaced.

## 8. Final security and health review

- [ ] normal members cannot open owner/admin management;
- [ ] revoked administrator/Staff sessions cannot continue privileged actions;
- [ ] private tickets, private events, Staff logs and Affiliate Stats remain
      private;
- [ ] read-only health does not mutate resources;
- [ ] `/server health` is reviewed again after the full journey;
- [ ] private logs contain no unresolved migration, permission or duplicate
      errors attributable to the candidate;
- [ ] any warning is linked to a focused Issue or has an explicit accepted
      rationale;
- [ ] rollback remains possible with the recorded backup and previous accepted
      commit.

# Decision rules

Mark `PASSED FOR THIS CANDIDATE` only when:

- every mandatory item above passed;
- optional `N/A` items have a recorded reason;
- no privacy/security stop condition occurred;
- no unresolved P0/P1 regression remains;
- the exact version/SHA and evidence summary are recorded;
- the owner explicitly accepts the result.

A pass means the core GamerHQ member journey is acceptable for this candidate.
It does not automatically:

- merge or tag the candidate;
- change `main`;
- deploy the VPS;
- run setup/repair;
- complete deferred roadmap features;
- approve a newer commit.

## Evidence template

Copy this bounded template into the release snapshot, a private owner record or a
GitHub comment that contains no private data:

```markdown
## Core Beta Acceptance

- Result: NOT STARTED | IN PROGRESS | BLOCKED | REJECTED | PASSED FOR THIS CANDIDATE
- Host version:
- Candidate SHA:
- Release branch:
- Environment: test guild | explicitly approved production acceptance
- Date/time:
- Owner/executor:
- Previous accepted version/commit:
- Verified backup exists: yes/no
- One active bot instance confirmed: yes/no
- CI 3.12/3.14: pass/fail
- Release readiness (candidate channel): pass/fail

### Section results
- Candidate/baseline:
- Profile/onboarding:
- Games:
- LFG/events:
- Temporary voice:
- Private support:
- Restart/persistence:
- Final security/health:

### Optional N/A items and reasons
- ...

### Findings
- Blockers:
- Manual-review items:
- Follow-up Issues:

### Owner decision
- Accepted for this candidate: yes/no
- Decision timestamp:
```

Never attach `.env`, runtime database contents, raw private logs, real support
text or member-private screenshots to this evidence.

## Completion boundary for Issue #134

The acceptance procedure is documented when this file is merged. Issue #134
should remain open until one exact reviewed release candidate completes the
procedure and the evidence records a final owner decision.
