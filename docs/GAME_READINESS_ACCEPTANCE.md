# STAB-03: truthful admin game readiness

## Status and scope

Planning and acceptance contract only; the runtime change is not implemented by this document.
Working branch: `fix/admin-game-readiness`.
Starting point: `feature/game-independent-events` at `e324f24af00211d69a1485fe6a6d04525a4f2255`.
Do not treat this documentation commit as a deployable feature fix.

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
| Unsaved user choice | Explicit Pending show / Pending hide | Cancel or replace the draft without a Discord write |

Green must mean that the visible game has its correctly associated text channel under the shared Games category, with the intended game-role access. The selectable flag or an unsaved choice alone is not proof of readiness. Unknown or failed reads must not be represented as a missing resource or successful setup.

A retained hidden chat is not the same as no chat. Preserve IDs, messages and member roles; hiding is not permission to delete history.

## Implementation boundaries

Reuse the existing `game_visibility_service` and admin selector. Do not build a second provisioner or persist another competing visibility flag.

- Read the actual selected branch, affected services and regression tests before editing.
- Determine readiness from a fresh, read-only configuration snapshot and the existing identity, operation-state and permission checks. Avoid message-history or whole-member scans.
- Resolve all states in a bounded shared inventory rather than one guild-wide API request per game.
- Preserve Popular, A-Z, pagination and choices across pages.
- Show page must include visible games needing setup; Hide page affects only that page. Retain the existing reviewed batch limit.
- Show / Hide and batch execution continue through the same confirmed lifecycle. Revalidate authorization and stale state before application.
- Render actual results after success or partial failure. No optimistic green while provisioning is pending or failed.
- Preserve current privacy, safe role hierarchy, additional-access review gates, manual-deletion markers, reservations and retry protection.
- Do not automatically recreate intentionally removed channels, merge unrelated PRs or deploy.

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
10. Add focused regressions and run the full locked CI against the exact candidate and target base before preparing an update.

## Owner workflow

Development, commits and review happen through GitHub. The owner does not download a ZIP or apply a local patch. After the implementation and checks are complete, provide the exact reviewed GitHub commit and the existing backup-first VPS update steps. The owner performs the update and Discord acceptance test.

For this documentation-only commit: no server pull, restart or Discord configuration change is required.
