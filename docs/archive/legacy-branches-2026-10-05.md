# Legacy Branch Archive — 2026-10-05

This document records the historical GamerHQ branches that were reviewed before branch cleanup.

The purpose is to keep a durable map from old branch names to their final commit SHAs and pull requests before deleting obsolete remote branches.

## Long-lived branches to keep

| Branch | Commit | Purpose |
| --- | --- | --- |
| `main` | `431fcc5b9b7db3495bbd7dfdba2f2b09ceec4d67` | Release / production |
| `develop` | `431fcc5b9b7db3495bbd7dfdba2f2b09ceec4d67` | Integration / development |

`develop` was fast-forwarded to the current `main` before this archive was written.

## Game / stabilization lineage

| Branch | Final SHA | PR | Status / reason safe to remove |
| --- | --- | ---: | --- |
| `feature/game-independent-events` | `5eb7e6c719d3aa70edfdd5c71ee47f66004a4400` | #1 | Closed. Its reviewed feature lineage is represented in later release PR #42 and current `main`. |
| `feature/game-system-v3` | `475f19ea7d7f9751135af0ea2089c48fbff8fcd3` | #2 | Closed as superseded. GAMES migration, legacy cleanup boundaries and Dissolve Event exist in newer `main`. The old six-hour expiry assumption is intentionally not retained; current `main` uses a two-hour automatic close. |
| `fix/owner-changelog-readable-groups` | `90ffbd9311331ec9479c07edb92ddcd9c88fa521` | #4 | Closed as superseded. Current `main` has newer readable summaries and owner-only Details/Undo. The one still-useful grouping idea is tracked as issue #44. |
| `docs/server-stabilization-roadmap` | `15e987b8e96d49be130ee567cadf08cfeefe58b6` | #7 | Closed as completed historical planning. STAB implementation work was delivered through subsequent PRs. |
| `feature/admin-game-visibility-selector` | `09ea85506c2e8b5170eb9d9609f3eb7b844b441f` | #6 | Merged into the historical integration branch, then represented in the release lineage. |
| `fix/games-visible-channel-lifecycle` | `2e15904575afbf7c5e2018740c6df5b2234aa582` | #5 | Merged. |
| `fix/owner-changelog-auto-detection` | `90c0926308301bff8671a18b25f3f4722746ed9c` | #3 | Merged. |
| `fix/support-entry-bindings` | `d2c2deb057918272b7778e91726d5326b7af6f26` | #8 | Merged. |
| `fix/ticket-component-roundtrip` | `21e041e0f3bfa4837be2916b40f6827a6e9d467a` | #9 | Merged. |
| `fix/support-direct-chat` | `634501bdb4faca19e3e93c7a9e91c218d1336b92` | #10 | Merged. |
| `fix/admin-game-readiness` | `a08a22bb7331e166dceb44602ff950ace6beea40` | #13 | Merged. |
| `fix/game-channel-recovery` | `47cc6831dbdc485517beab31941e49ca5df405f9` | #14 | Merged. |
| `fix/amazon-marketplace-permissions` | `236a6a7320ff5a1653990aa59164154e7ed147fe` | #15 | Merged. |
| `fix/get-started-server-tour` | `3c5137e0a7fe6bc113c796be7c6e2a1addb04c6e` | #16 | Merged. |
| `fix/owner-changelog-noise` | `63dd52b1d4aada81802d2014781d92d54f07f82c` | #17 | Merged. |
| `fix/owner-changelog-direct-summary` | `ed961e23b9096d3bad35c9583c38ab303555997a` | #18 | Merged. |
| `feature/server-check-dashboard` | `2718f379e4bc626156a93b242fc53cc6c9be9569` | #19 | Merged. |

## Skill Platform lineage

The Skill Platform was developed as a stack of small reviewable branches, then consolidated through release PR #42 and final external-repository extraction PR #43.

| Branch | Final SHA | PR | Status / reason safe to remove |
| --- | --- | ---: | --- |
| `feature/portable-skill-runtime` | `9d15e0570bd4685c2a2ff68f00e13265ab90c817` | #20 | Obsolete first runtime attempt; superseded by clean runtime lineage. |
| `feature/portable-skill-runtime-clean` | `e86641d74604e852fbe539fefa75a758748f5273` | #21 | Superseded by #42. |
| `feature/skill-persistent-scheduler` | `5da8372dc450a604aa7057e7485107dd2eedbe33` | #22 | Superseded by #42. |
| `feature/skill-gamerhq-runtime` | `6951a75b3b2dfab14d29214e2c389c9a5b41e359` | #23 | Superseded by later host composition. |
| `feature/skill-gamerhq-host-runtime` | `94a13e6cf61872d691ddf880d29f80b580884c1a` | #24 | Superseded by later host composition. |
| `feature/skill-management-ui` | `cdc95c31d85fb8ae1c4a09c4dee682bffb91a8d5` | #25 | Superseded by later Skills management UI. |
| `feature/skill-discord-message-adapter` | `cd99d14e63e2fb53e5b389ef526670f2e1c1030f` | #26 | Superseded by later Discord adapter. |
| `feature/skill-host-bootstrap` | `c82dcac3624c87aa32fe8137390c936331307295` | #27 | Superseded by later composition. |
| `feature/skill-discord-adapter-ui` | `c2744a2e82c015f677b32d0d5abbfd4dc1b44a62` | #28 | Superseded by #42. |
| `feature/recurring-posts-skill` | `4a5ae20cda407a29d34b4ad717974f738f71bae4` | #29 | Superseded by external-package Recurring Posts. |
| `feature/external-skill-packages` | `e1dad596d82ee6cec75f1f50ddc94d89e4eb248b` | #30 | Superseded by #42/#43. |
| `feature/skill-sdk-package` | `e2b5769d07596bc7591dc6c7032116b4a45bfe95` | #31 | Superseded by #42. |
| `feature/skill-authoring-guide` | `aaa3b3dc7a906f015a5f16de7fab56b6e082b008` | #32 | Superseded by #42. |
| `feature/skill-conformance-tools` | `50e1be752b8b70c7e81c902d827dda3772968d95` | #33 | Superseded by #42. |
| `feature/skill-source-audit` | `6a3188f278e2f015907f06763a473d04e1b4df07` | #34 | Superseded by #42. |
| `feature/external-skill-dry-run` | `d12fb6d809fdb7c05479ccf6142b96fd18342be0` | #35 | Superseded by #42/#43. |
| `feature/static-skill-permissions` | `e4ba0ad996d30ffc6608d9af467621f66dd533e2` | #36 | Superseded by #42. |
| `feature/skill-package-provenance` | `1f857d28d46bec0ac9a43f1cfa8856d6b72382c5` | #37 | Superseded by #42. |
| `feature/external-skill-failure-isolation` | `64f6d07c7b34174fa881030f5e2ee37ebacd9580` | #38 | Superseded by #42. |
| `feature/skill-management-contract` | `6958eaf3057f8a0fe539afa5ccb9c47b47eb0d70` | #39 | Superseded by #42; extraction docs copied to final release before cleanup. |
| `feature/skill-production-packaging` | `4db1956dfe967e7275b4868c166b3262d3793e39` | #40 | Superseded by #42/#43. |
| `feature/recurring-posts-external-package` | `b86585f90c9b3261a446b7721451e394bc9c2d18` | #41 | Superseded by #42/#43. |
| `release/skill-platform-v1` | `615f3116a17a24d83364c673a22572e8c70c155b` | #42 | Merged to `main` as the Skill Platform v1 release. |
| `feature/install-recurring-posts-from-external-repo` | `fb8d11c055303bfcb3063ddf271d993235429559` | #43 | Merged to `main`; Recurring Posts now installs from its standalone repository. |

## Skill prototype branches without a final dedicated PR

These were intermediate experimental branches that were replaced by the reviewed Skill Platform stack and are not active release paths.

| Branch | Final SHA | Disposition |
| --- | --- | --- |
| `feature/external-skill-lockfile` | `64f6d07c7b34174fa881030f5e2ee37ebacd9580` | Prototype only; final lock workflow is in current `main`. |
| `feature/skill-recurring-posts` | `fa3efa08aec199862e8ed1502dbe13e21abc3277` | Prototype only; replaced by external Recurring Posts repository. |
| `feature/skill-resource-grants` | `cdc95c31d85fb8ae1c4a09c4dee682bffb91a8d5` | Prototype only; capability/grant behavior is represented in final Skill Platform. |

## Recovery references

Closed pull requests remain the primary historical discussion/review reference.

For old unmerged drafts with unique historical implementation details:

- PR #2 — Game System V3 and safe event cleanup
  - final branch SHA: `475f19ea7d7f9751135af0ea2089c48fbff8fcd3`
  - important retained decisions were reviewed before closure
  - obsolete six-hour expiry assumption was intentionally not copied to current `main`
- PR #4 — readable/grouped Owner Change Log
  - final branch SHA: `90ffbd9311331ec9479c07edb92ddcd9c88fa521`
  - current `main` already contains the newer readable-summary/Undo design
  - remaining grouping idea is tracked as issue #44

## Cleanup rule

After this archive is merged, all remote branches listed above may be deleted except:

- `main`
- `develop`

Deleting a branch removes an obsolete branch pointer; it does not remove the merged current implementation from `main`.

Future work should create fresh branches from the current `develop` or `main`, rather than reviving these historical branches.
