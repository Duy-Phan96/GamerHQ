# STAB-03: truthful admin game readiness

## Status and scope

Implementation and acceptance contract for `fix/admin-game-readiness`.
Starting point: `feature/game-independent-events` at `e324f24af00211d69a1485fe6a6d04525a4f2255`.
Canonical CI for the final PR commit and owner-controlled live acceptance are required before calling this deployed. The original planning-only commit was `8269cb4`.

The owner has reported that the direct Support chat now works and has requested the next stabilization topic. This is owner-reported acceptance of the basic flow, not independent verification of every permission, restart or legacy-data scenario. The previous feature is PR #10; the future staff assignment overview remains issue #11.

This slice concerns the existing admin game selection, not members' personal Choose Games, Support, Electricity, Amazon permissions, the welcome tour or Owner Change Log.

## Required presentation and click behavior

| State | Presentation | First click |
| --- | --- | --- |
| Visible and verified ready | Green check: Ready | Stage Hide |
| Hidden and no associated text channel | Neutral plus: Not set up | Stage Show / Set up |
| Visible but text channel absent or incomplete | Warning: Setup needed | Stage Show / Set up, never toggle to Hide first |
| Hidden with retained channel and history | Hidden - history kept | Stage Show using the same channel |
| Invalid, conflicting or unverified state | Needs review, with a useful reason | Do not imply readiness or delete/recreate resources |
| Unsaved user choice | Blue: Show pending / Hide pending | Cancel the draft without a Discord write |

Green means that the visible game has its correctly associated text channel under the shared Games category, with the intended game-role access. The selectable flag or an unsaved choice alone is not proof of readiness. Unknown or failed reads are not represented as a missing resource or successful setup.

A retained hidden chat is not the same as no chat. Preserve IDs, messages and member roles; hiding is not permission to delete history.

## Implementation

The read-only `game_readiness_service` is a presentation adapter over the existing `game_visibility_service` planner/snapshot and `game_channel_service` access policy. It does not provision resources, persist another visibility flag or introduce a background synchronizer.

Opening and Refresh fetch one shared channel and one role inventory, with read-only DB access and rechecked administrator authorization. Readiness combines canonical IDs, the shared category, role, name, access policy, hide flag, existing operation status and retained creation reservations. Failed inventory produces Unverified for all affected games. Per-game errors remain separate review reasons.

The existing `game_visibility_selector` preserves Popular, A–Z, 15-game pages, page-scoped Show/Hide and its 50-choice batch review. A visible incomplete game stages setup on its first click. A second click cancels its draft. Existing history-preserving writes, current authorization and stale-signature checks remain in the original lifecycle.

Readiness describes the last successful snapshot, not a live subscription. After 90 seconds, subsequent renders/clicks ask for Refresh before staging additional unverified games. No background message edits are scheduled. Pending choices remain blue and may be cancelled. The existing confirmation obtains its own fresh review.

Refresh retains the original catalog version for every pending choice, so it does not silently rebase a draft over another administrator's edit. Back from review refreshes the displayed facts while retaining choices. Back to games after results clears the applied draft and reads actual state anew, including partial or uncertain outcomes; it never retries failed creations automatically.

The adapter does not loosen existing additional-access, manual-deletion, identity or role-hierarchy gates. No messages, memberships or server resources change merely by opening, refreshing, browsing or cancelling this panel. No schema, environment or dependency changes are required.

## Acceptance gates

1. Visible game without a chat is not green; clicking it stages setup.
2. Visible game with a valid role-gated chat in Games is green; clicking it stages Hide.
3. Hidden game without a channel is distinct from a hidden retained channel.
4. Wrong category, missing role, unsafe access, stale mapping and unfinished operation are not falsely ready.
5. Unsaved choices and pending operations are distinguishable from applied states.
6. Page changes preserve staged choices; Show page includes missing chats.
7. Cancelling performs no writes. Non-admins and revoked admins cannot apply changes.
8. Repeated or concurrent confirmation creates no duplicates; partial failures remain visible.
9. Member game selection stays role-only. Existing channels, histories and ticket functionality are preserved.
10. Run focused regressions and full locked CI against the exact candidate and target base before preparing an update.

## Validation commands

```bash
python -m pytest tests/test_game_readiness.py tests/test_admin_game_visibility.py
python -m pytest
python -m tools.repository_audit --history
git diff --check
git status --short
```

The new readiness tests exercise missing chats, real Show/Hide outcomes, malformed/pending state, reservations, actual versus cached inventory, read denial, bounded API counts, authorization loss, stale snapshots, refresh-preserved drafts and post-apply partial failures. Existing bulk tests retain their security, capacity, history, pagination and concurrency cases; two obsolete flag-only display expectations now follow the requested readiness semantics.

## Owner workflow

Development, commits and review happen through GitHub. The owner does not download a ZIP or apply a local patch. After the final commit passes CI and is reviewed, provide the exact reviewed GitHub commit and the existing backup-first VPS update steps. The owner performs the update and Discord acceptance test.

After updating, open `/server manage` → Games → Manage Visible Games. Compare one already-ready game with one visible game still missing its chat. Stage the missing setup, change page, return and confirm the review. Use Back to games to verify the new ready state. Hide it and check the distinct retained-history state, then show it again and confirm the same channel/history returns. Use a normal game-role member to check visibility; owner/staff access alone does not prove it. Test cancellation and an additional-access review without removing unrelated permissions.

No live server or Discord acceptance is implied by this document or an offline test result. The deeper blocked-game repair flows, Amazon access and welcome tour remain separate roadmap slices.

See [game system](GAME_SYSTEM.md) for the normal lifecycle and [development workflow](DEVELOPMENT_WORKFLOW.md) for isolated tests.
