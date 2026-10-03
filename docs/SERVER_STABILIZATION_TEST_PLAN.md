# Server stabilization acceptance plan

Companion to the [repair queue](SERVER_STABILIZATION_ROADMAP.md). Reviewed baseline: `ffd039f6df274aa559bd3ed4231212c3c8612306`, 2026-10-01.

**Future live cases are NOT RUN until an actual result is recorded.** These are specifications, not successful tests. This documentation changes no application code, permissions, data or Discord resources.

## Discipline

Follow [tests/AGENTS.md](../tests/AGENTS.md) and the [development workflow](DEVELOPMENT_WORKFLOW.md). Use synthetic users/content and offline isolation. Never import production DBs or start bot.py for validation. Live acceptance uses one existing VPS bot and a small approved scope; never delete real chat history or expose private tickets as a test.

Record date, actual running commit, actor class, case IDs, expected/observed outcomes and partial changes. Keep production IDs, detailed logs, private contents and backup references private. Check real ordinary-member access: owner/admin visibility does not prove a private-channel deny failed. No mutation in a purported read-only health check; a cache miss is not proof of deletion.

## Support/electricity entry binding

| Case | Setup/action | Required result |
| --- | --- | --- |
| A01 | Current same-bot Need Support entry; consistent settings and managed-message record | Opens form, no ticket before submit |
| A02 | Independently omit/mismatch each entry channel/message mapping in a fixture | Exact failed comparison diagnosed; reject safely, no duplicate or custom-ID-only acceptance |
| A03 | Click obsolete, wrong-guild/channel or lookalike entry | Only link to verified accessible canonical entry; unsafe message cannot create ticket |
| A04 | Customized entry, duplicate candidates, replaced bot identity or denied access | Preserve text/controls; ambiguity requires scoped review, no overwrite |
| A05 | Fail ticket recovery; separately remove unrelated partner mapping; restart | Healthy entry refresh independent; electricity retains household-key compatibility; repeated repair idempotent |

Diagnostics compare settings AND managed-message metadata. Report missing/matches/mismatch/unavailable/denied without exposing configuration values. Rebinding is a separate confirmed action with bot-authorship/fingerprint checks.

## Ticket lifecycle

| Case | Setup/action | Required result |
| --- | --- | --- |
| A06 | Members A/B each open synthetic support tickets | Creator/intended staff access only; no other member/external bot access; one recorded channel/message each |
| A07 | Concurrent/repeated form and electricity submissions; per-type limits | Return correct existing ticket, no duplicate; electricity remains typed private request |
| A08 | Staff takes/waits; authorized close; unrelated member attempts actions | Authorization checked; close confirmed; history retained, creator read-only |
| A09 | Restart with open/closed records, cache miss, delayed creation, failed pin/send | Original known IDs preserved; accurate partial state, safe recovery, required permissions reported |

The electricity button is not an external tariff-search API. Verify current pin permissions for operations used. Missing cached membership alone must not mark a user departed. Never restore production SQLite merely to fix one entry.

## Games readiness and migration

| Case | Setup/action | Required result |
| --- | --- | --- |
| A10 | Active/selectable game with absent/stale/wrong-type/unverified channel | Never green; needs setup/review; click stages Show/setup, not Hide |
| A11 | Verified exact channel/parent/role/access and no pending operation | Green Ready; ready count agrees |
| A12 | Off without chat vs off with retained hidden chat | Distinct honest states; No channel only for actual absence; no implicit deletion |
| A13 | Stage Show/Hide across pages; back/clear/cancel/reopen | Draft vs current distinct; no pre-confirm mutation; unvisited games untouched; fresh status on reopen |
| A14 | Partial batch failure, denied rights/API verification, restart/retry | Per-game result, not global green; no repeated known successful creation |
| A15 | Hide/Show same chat as ordinary member and owner | Preserved ID/history; member access correct; staff exception clear; hidden chats count toward capacity |
| A16 | Stored legacy chat loses old category, or canonical chat is elsewhere | Reviewed migration preserves ID/history; channel_id alone not proof ready |
| A17 | Extra access, conflicting mappings, 403 vs 404 vs cache miss | Specific private diagnosis; no blanket ACL wipe/name-only adoption; same safeguards across migration routes |
| A18 | Repeat setup/migration; reopen older panel | No duplicates; counts refreshed; only Games positions intentionally sorted |

Retain review and cumulative capacity limits. Test new and migrated/customized chats. Decision D1 in the roadmap remains a separate destructive-product decision: tests do not authorize deletion; retained hidden chats must be labeled honestly.

## Amazon bot / Marketplace

| Case | Setup/action | Required result |
| --- | --- | --- |
| A19 | Configure exact bot and Amazon destination with lookalike present | Verified identity only; no name-based trust or token in GamerHQ |
| A20 | Approved harmless message/card to selected channel; try private/unselected targets | Intended posting works; no ticket/Staff/purchases/buyer-ranking or all-Marketplace grant |
| A21 | Repeat ordinary read-only/Marketplace repair and restart | Bot posting retained; members remain read-only; no duplicate grants/posts or privilege escalation |
| A22 | Rotate/remove bot; optional content-conversion module off/on | Revoke only owned grants after confirmation; intents checked per module; no automatic moderation/deletion rights |

Permission checks are not product-data API/affiliate eligibility checks. Make no purchases and request no credentials in chat. Category access does not automatically justify posting on every child.

## Get Started tour

| Case | Setup/action | Required result |
| --- | --- | --- |
| A23 | New/existing member opens original persistent welcome entry | Real tour, profile separate/optional; no forced age/gender |
| A24 | Previous/Next/Skip/Close, personal selector detour, resume/replay/restart | Clear progress; viewing creates no roles/pings/events/tickets/voices |
| A25 | Renamed/moved/missing/inaccessible destination or disabled module | Current identity/access respected; no private/stale/fabricated links; optional extras omitted |
| A26 | Mobile/narrow layout, long names, completed profile, customized welcome | Readable pages/actions; no duplicate profile flow/unreviewed copy overwrite |

Do not advertise functions that have not passed relevant checks. Native Discord Onboarding remains separate; this tour reuses the bot's welcome entry.

## Owner log and release

| Case | Setup/action | Required result |
| --- | --- | --- |
| A27 | Remove approved disposable category, preserve its three test channels | Group reliable detaches/deletion; independent name/permission changes remain |
| A28 | Genuine sibling reorder vs absolute-position shifts after unrelated change | Genuine move retained; collateral event explained/grouped, not falsely attributed; raw history retained |
| A29 | Restart/old notice/non-owner/unsupported Undo/long historical name | Secure persistent binding; honest names/actors; no unusable Undo; no public private details |
| A30 | Reviewed exact-tree CI, backup/preflight and approved update | Actual container commit/health and affected entries/rights verified; restart check and rollback limits recorded |

Broader future smoke cases, also NOT RUN here: personal game roles, admin review/cancel, LFG create/join/leave, current temp voice, suggestions, profile preferences, feeds, managed pins and staff access. No mass migration/cleanup as a test.

## Existing offline entry points

Run in isolated development/CI, **not against production data on the VPS**. Add focused regressions for new cases; do not disable existing tests.

```bash
# STAB-01/02
python -m pytest tests/test_tickets.py tests/test_energy_offers.py tests/test_managed_messages.py tests/test_interactive_read_only.py tests/test_support.py -q
# STAB-03/04
python -m pytest tests/test_admin_game_visibility.py tests/test_game_visibility.py tests/test_game_channels_v2.py tests/test_game_system_migration.py -q
# STAB-05
python -m pytest tests/test_marketplace.py tests/test_bot_organization.py tests/test_instant_gaming.py tests/test_interactive_read_only.py -q
# STAB-06: add dedicated tour regressions when implemented
python -m pytest tests/test_profile_wizard.py tests/test_onboarding.py tests/test_role_settings.py -q
# Final release gates
python -m pytest
python -m pip check
python -m tools.repository_audit --history
git diff --check
git status --short
```

Use the existing locked Python 3.12/3.14 CI matrix and shell/Compose/image checks. Do not call a tree containing unrelated PR changes the tested release. A docs-only change does not need a new application test run; report that distinction.

## Acceptance record template

```text
Task ID:
Source/head and actual merge commit:
CI run / interpreter / passed, failed, skipped, warnings:
Reviewed scope and known limits:
Owner-approved update/confirmation scope:
Previous image / verified DB backup reference (private):
Actual running container commit:
Cases executed:
Expected / observed / PASS, FAIL or NOT RUN:
Partial DB/Discord changes:
Restart check:
Rollback or next targeted repair:
Owner acceptance:
Queue status:
```

## External implementation references

Checked 2026-10-01. Recheck when implementing; these describe Discord, not the live VPS configuration.

- [Discord permissions](https://docs.discord.com/developers/topics/permissions): effective permissions, administrator bypass and operation-specific flags including pinning.
- [Discord Gateway](https://docs.discord.com/developers/events/gateway#message-content-intent): intents distinct from channel grants.
- [Discord components](https://docs.discord.com/developers/components/reference#button): supported styles; additional states use text/icons.

## Current handoff

Documentation only. No support mapping, welcome entry, game button, ACL or live bot changed. Begin STAB-01 and record evidence before marking tickets repaired. No server pull/restart required to read the plan.
