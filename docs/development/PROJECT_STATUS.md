# GamerHQ Project Status

_Discord readiness baseline verified 2026-11-09. Historical assessment retained below._

This document is a concise strategic snapshot of the GamerHQ repository. It is
not a second operational backlog and does not replace GitHub Issues, pull
requests, the changelog, release snapshots or feature-specific documentation.

The snapshot was prepared from the current repository, code, tests,
documentation, Issues and pull requests. Important claims should continue to be
verified against implementation because documentation may lag behind code.

## Current execution and release evidence

**GamerHQ Discord is the immediate priority. GamerHQ Web is frozen.** Only the
owner can resume Web work. Web features, deployment and staging activation
are paused while the platform brand and commercial domain are undecided.
Preserve existing Web work and public API compatibility. GamerHQ is the
first customer of the generic Host and Runtime. Each external Skill owns its
domain, contracts and release.

| Surface | Evidence and next gap |
| --- | --- |
| Staging | [Live smoke](https://github.com/Duy-Phan96/GamerHQ/actions/runs/42602882154) confirmed Host `29ecf575f3d865b0f32a9d82adf1b06d7969cdc9` and Event Manager `0.1.2`. The smoke covers the running Host and Skill. Guild layout, member journeys and the next candidate still need live acceptance. |
| Production | `main` identifies `1.0.0-beta.2-rc2`. GitHub has no production deployment receipt for this audit. Confirm installed image, Host commit, Skill set, backup freshness and the member journey before any promotion. [Release PR #150](https://github.com/Duy-Phan96/GamerHQ/pull/150) is open. |
| Setup and staff operations | The repository has read-only Server Check, preview/confirm setup, repair and reconciliation, managed messages, and a private owner change log with bounded Undo. Existing permission, mapping, stale-plan and retry tests apply. Live resource drift and recovery evidence are still required. |
| Member entry | Profile panels, optional age bands (Under 18, 18–20, 21–22, 23–24, 25+), platform/preferences and a personal game selector exist. The native Discord onboarding question editor stores Host-side desired state and shows a preview. Native publishing and option lifecycle are separate gaps. Preserve existing age/profile role IDs. |
| Games and LFG | The public game board leads to the private selector. Role assignments use the reviewed game mapping, and the Games structure allows optional role-gated text channels. Temporary LFG retains its own capacity, invitation, privacy and recovery paths. Confirm the new-member and private-LFG paths in the isolated guild. |
| Event Manager | Installed external Skill `0.1.2` is healthy in staging. Discord V2.1 manual acceptance remains postponed in [#208](https://github.com/Duy-Phan96/GamerHQ/issues/208). The [separate #226](https://github.com/Duy-Phan96/GamerHQ/pull/226) tracks newer staging work. |
| Progression and Recurring Posts | Progression `0.2.0` is source-pinned and the Host emits trusted signals through `progression.record-signal.v1`. Recurring Posts `1.2.3` remains source-pinned although `1.4.0` is published. Verify compatible wheels, each guild's enablement and private notification configuration independently. See the [Skill handoffs](../skills/platform-customer-readiness.md). |

The first Host candidate for this workstream fixes a stuck personal game
selector after Discord acknowledgement failure and flags unsafe or shared
game-role mappings in Server Check. It requires no data migration or
external Skill upgrade. Before staging apply, review an exact immutable Host
pin against the installed Event Manager `0.1.2`, verify a fresh backup and
restore probe, and use the existing protected Host promotion. A staging
member should then open the public Games board, add and remove one game in
the private selector, and verify the matching role and channel access.
Staff should check **Games** in `/server health` without applying a repair.

## Executive status

GamerHQ is an **advanced beta** rather than a prototype.

It currently serves two related purposes:

1. a Discord gaming-community product for finding players, organizing sessions,
   managing voice rooms and operating the community safely;
2. the first/reference customer for a portable Skill Runtime / SDK and a future
   thinner Discord Host.

The immediate priority is consolidation, not another broad feature expansion.
The repository already has strong safety, testing and deployment foundations;
the remaining risk is excessive scope and a historically broad Host.

## Product vision

The direct GamerHQ member value remains:

> Find Games. Find Mates. Play Together.

The primary product loop is:

```text
join community
    -> configure profile and games
    -> find or create an LFG/event
    -> join voice and play
    -> return to the community
```

The long-term platform direction is:

```text
GamerHQ Discord server
        -> thin GamerHQ Host / server observation
        -> public Runtime / SDK / capabilities
        -> independently released installed Skills
```

GamerHQ is the reference customer, not the platform boundary.

## Verified maturity and health

| Dimension | Rating | Current assessment |
| --- | ---: | --- |
| Product maturity | 4/5 | Advanced beta with real production operations |
| Architecture | 4/5 | Strong Runtime/Host boundary; historical Host remains broad |
| Feature completeness | 3.5/5 | Core community workflows exist; roadmap is much wider |
| Documentation | 4/5 | Extensive and professional, with some drift/duplication risk |
| Code quality | 3.5/5 | Strong safety patterns; several large management modules |
| Testing | 4.5/5 | Broad offline regression suite and synthetic Discord isolation |
| CI/CD | 4/5 | Python 3.12/3.14, package/build, preflight, audit and Compose gates |
| Security | 4/5 | Least privilege, scoped capabilities, backups and secret controls |
| Deployment | 3.5/5 | Reproducible scripts and rollback; release metadata needs cleanup |
| User experience | 3.5/5 | Good Preview/Confirm patterns; admin UX is historically grown |
| Business readiness | 3/5 | Strong technical base; external adoption is not yet validated |

These ratings are qualitative assessments. They are not code-coverage, adoption
or revenue metrics.

## Major strengths

### Safety-first operations

The repository consistently uses preview/confirm flows, stale-state checks,
authorization rechecks, idempotent repair, private access verification, verified
backups, migration rehearsal and explicit rollback procedures.

### Portable Skill architecture

The intended dependency direction is clear:

```text
Skill
    -> portable Runtime / SDK
    -> Host ports
    -> GamerHQ adapters
    -> Discord.py / SQLite
```

External Skills must not import private GamerHQ implementation code.

### Test and CI discipline

The test suite covers core community behavior, Runtime/SDK boundaries,
production safety, external Skill installation, authorization, restart/retry and
migration behavior. CI includes Python 3.12 and 3.14, dependency validation,
portable compilation, SDK wheel build, release preflight, full pytest,
repository audit, shell validation, Compose validation and production image
build.

## Main weaknesses and risks

### The Host is still broad

The current application contains many historical domain features in `cogs/`,
`services/` and `database/`. This is acceptable during transition, but should not
become the permanent platform boundary.

### Release metadata is inconsistent

At the time of this review, `main` identifies the accepted rc6 release while
`develop` contains newer functionality but still carries an older `VERSION`
value. The repository needs one documented rule for what `VERSION` means on
`develop` versus `main` and release-candidate branches. This is tracked in Issue
#133.

### Documentation drift

The README still describes the general support entry as a short form, while the
current implementation opens a simple private support chat without a required
form. Small mismatches like this should be corrected during consolidation.

### Scope remains large

GamerHQ currently spans community operations, gaming/LFG, voice, profiles,
onboarding, tickets, affiliate integrations, Runtime/SDK, Host Web APIs and a
large future Skill roadmap. New feature lines should be deferred until the beta
core and release process are coherent.

## Follow-up completed from this audit

- PR #129 completed the read-only Staff Ticket Overview and merged with green
  Python 3.12/3.14 CI.
- The release-note metadata regression was fixed by testing the current
  `[Unreleased]` bullets semantically instead of expecting one unrelated bullet
  at a fixed position.
- Issue #63 was closed in GamerHQ with a handoff because `gamerhq-web` owns that
  implementation.
- Issue #133 now tracks Host `VERSION` semantics.
- Issue #134 now tracks the measurable core-beta member journey.
- Issue #135 now tracks the thin-Host ownership inventory.
- Issue #136 now tracks repository/SDK licensing clarity.

## Completed strategic milestones

### Community and member foundations

- game selection and optional role-gated game channels;
- public/private LFG and scheduled events;
- temporary voice and member controls;
- member profile and onboarding management;
- Server Booster experience;
- private support tickets and audit lifecycle;
- staff-only active ticket assignment overview;
- managed server setup, repair, health and messaging.

### Skill Platform foundation

- portable Runtime/SDK contracts;
- capabilities and Host enforcement;
- Skill lifecycle, storage, secrets and scheduler;
- Management APIs and Management UI Schema;
- external package discovery and immutable pins;
- Host Web API surfaces;
- SDK packaging and conformance tooling.

### Independent Skills / integrations

- Recurring Posts externalized;
- XP & Progression externalized;
- Awin Affiliate integrated from a reviewed standalone package;
- read-only scoped scheduler job inspection added to SDK 0.2.0.

## Active consolidation work

### Release/version policy — Issue #133

Define one testable rule for `VERSION` on `develop`, release-candidate branches
and `main` without creating a second version source.

### Core beta acceptance — Issue #134

Verify the real member journey across onboarding/profile, games, LFG/events,
voice, support and restart stability.

### Thin Host inventory — Issue #135

Classify current domains as Host primitive, keep temporarily, Skill candidate or
legacy/retire before selecting another extraction.

### Licensing — Issue #136

Clarify whether MIT applies to the complete repository or only the packaged SDK
before broader third-party adoption.

## Roadmap recovery

### Committed / active scope

- consolidate release metadata and version semantics;
- define and verify the core-beta member journey;
- preserve the thin-Host direction;
- maintain repository ownership and handoff boundaries;
- inventory the current Host before the next extraction;
- continue extracting mature domain functionality only when contracts and
  storage/lifecycle behavior are stable.

### Proposed future scope

- Event Manager recurring events, followed by standalone Skill extraction readiness (Issue #153);
- reminders;
- polls/community decisions;
- creator/streamer Skill;
- giveaways;
- game-stat integrations;
- additional affiliate/provider Skills;
- Member Profile extraction when its boundary is mature.

### Explicitly deferred

- tournament brackets/check-in/history;
- opaque moderation/reputation automation;
- broad new web work from this repository;
- additional platform abstractions without a concrete Skill requirement.

The existing event system is functional; tournaments remain a later specialized
event capability.

## Recommended milestones

### Milestone 1 — Beta consolidation

**Status:** In progress

**Objective:** make the current development state coherent, documented and
release-ready before adding another broad feature line.

Completed:

- Staff Ticket Overview;
- semantic release-note metadata regression test;
- gamerhq-web ownership handoff for the old Host issue.

Remaining:

- define `VERSION` behavior across `develop`, release candidates and `main`;
- correct README/documentation drift;
- decide repository/SDK licensing and add the appropriate license file(s).

### Milestone 2 — GamerHQ core beta

**Status:** In progress

**Objective:** validate the real community journey rather than only individual
features.

Acceptance journey:

```text
member joins
    -> profile/onboarding works
    -> game selection works
    -> LFG/event creation and joining works
    -> temporary voice works
    -> support works
    -> restart preserves state and creates no duplicates
```

Issue #134 is the operational acceptance record.

### Milestone 3 — Thin Host inventory and extraction plan

**Status:** Planned

Classify existing Host modules as:

- Host primitive;
- keep temporarily;
- Skill candidate;
- legacy / retire.

Do not begin another extraction until Issue #135 identifies a stable,
high-value boundary.

### Milestone 4 — SDK / third-party readiness

**Status:** In progress

Focus on public compatibility policy, independent SDK distribution, developer
experience, licensing and proportional security/dependency automation.

### Milestone 5 — Event Manager maturation and extraction

**Status:** Planned

Extend the current event system with recurring events first, then prepare the
Event Manager for extraction into a standalone Skill. The sequence is:

1. add recurring series with timezone/DST-safe scheduling and explicit
   occurrence-versus-series editing/cancellation;
2. live-accept one-time and recurring event behavior;
3. separate domain state from Discord resource adapters and publish stable event
   lifecycle contracts;
4. perform extraction-readiness review;
5. create the standalone Event Manager Skill repository through the normal
   repository handoff process.

Issue #153 is the operational record for this work. GamerHQ remains the
first/reference customer after extraction.

### Milestone 6 — Growth features

**Status:** Future

After consolidation and Event Manager stabilization: reminders, polls,
giveaways, creator integrations, game stats and additional provider Skills.

## Prioritized execution backlog

### P1 — high value

1. Issue #133 — define and test `VERSION` semantics for `develop`, release
   candidates and `main`.
2. Issue #134 — execute the GamerHQ core-beta acceptance journey for the next
   reviewed release.
3. Issue #135 — produce the thin-Host ownership inventory before selecting the
   next extraction.

### P2 — important maintenance

4. Correct README and command-documentation drift.
5. Issue #136 — decide whether MIT applies to the whole repository or only the
   SDK; add explicit license file(s) accordingly.
6. Evaluate Dependabot and CodeQL/security scanning proportionally to current
   project maturity.

### P3 — planned / postponed

7. Issue #153 — add recurring events to the Event Manager and prepare its
   standalone Skill extraction.
8. Reminders and polls.
9. Tournaments, game stats and moderation/reputation features.

## Decisions still required

1. What does `VERSION` represent on `develop`?
2. Does MIT license the complete repository or only `gamerhq-skill-sdk`?
3. What measurable evidence completes the core-beta journey in Issue #134?
4. Which domain area should be the next extraction candidate after Issue #135?

## Recommended next release objective

The next GamerHQ release should be a **post-rc6 beta consolidation release**, not
a broad feature release.

Candidate scope:

- already-reviewed Awin Host integration;
- Owner Change Log grouping;
- SDK 0.2.0 scheduler inspection;
- Staff Ticket Overview;
- release/documentation consistency fixes.

Do not add tournament expansion, new game APIs, moderation automation or another
large platform initiative to that candidate.

## Next five actions

1. Resolve release/version metadata semantics in Issue #133.
2. Correct focused README/documentation drift.
3. Prepare and execute the core-beta acceptance plan in Issue #134.
4. Perform the thin-Host inventory in Issue #135.
5. Resolve licensing scope in Issue #136.

## Maintenance rule

Update this snapshot only after a meaningful audit, milestone or release change.
Use GitHub Issues and pull requests for day-to-day execution. Do not turn this
file into a duplicate task board.
