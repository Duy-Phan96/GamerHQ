# GamerHQ server stabilization roadmap

Status: **audited backlog; not an implemented repair**. Updated: 2026-10-01.

Work one repair slice at a time. The owner uses ChatGPT/GitHub and the VPS terminal; no download, local checkout or ZIP deployment should be required on the owner's computer.

Audited remote source: `feature/game-independent-events` at **ffd039f6df274aa559bd3ed4231212c3c8612306**. This is not verification of the running container. Live evidence consists of the owner's screenshots/reports, not direct access to Discord, the VPS, runtime database or logs.

Use the [acceptance plan](SERVER_STABILIZATION_TEST_PLAN.md), [AGENTS.md](../AGENTS.md), [operations](PRODUCTION_OPERATIONS.md) and [rollback](../ROLLBACK.md). No application changes, automatic merge or deployment are included in this documentation task.

## Evidence rules

**Observed** means shown/reported by the owner. **Code-confirmed** means supported by the pinned source, not proof of a particular live cause. **Hypothesis** needs a targeted check. **Planned** is not implemented. Reserve **Live verified** for a recorded owner test of the actual running commit.

No issue is fixed merely because a PR exists, CI is green or an image builds. Keep screenshots, actual production IDs, ticket contents, configuration, DBs and logs out of this public repository. No source from the separate private Amazon bot repository is copied here.

| Ref | Observation | Limits of the evidence |
| --- | --- | --- |
| E1 | Create Support Ticket replies `Open the current Need Support entry.` | Handler responded; which ID/mapping differs is unknown; creation backend not reached |
| E2 | Electricity replies `Please use the current message in the Electricity channel.` | Entry rejected; not evidence of a tariff-provider/API failure |
| E3 | Get Started opens profile setup, but owner wants a server tour | UX requirement, not itself a VPS callback outage |
| E4 | Games category exists; new chats and `Migrated 6 game chat(s)` reported | Visible progress, not proof all games/permissions are correct |
| E5 | Some games report extra access grants or unavailable saved chats | Requires individual review; not permission to erase IDs/overwrites |
| E6 | Amazon Affiliate Bot needs Marketplace permissions | Exact identity, enabled modules and effective live access remain unverified |
| E7 | Owner requests green = visible AND text channel ready; off = not visible/no text channel | Selector contract must change; does not authorize history destruction |
| E8 | Earlier owner-log entries showed position-only changes on apparently untouched channels | Classify collateral changes; do not assume every position event is false |

## Ordered queue

All items start **Planned**. P1 = broken core journey, misleading state or integration access; P2 = usability/operational improvement. No P0 privacy incident has been established.

| Order | ID | Priority | Slice | Gate |
| --- | --- | --- | --- | --- |
| 1 | STAB-01 | P1 | Support/electricity canonical entry binding | Read-only diagnosis first |
| 2 | STAB-02 | P1 | Full private-ticket lifecycle after restart | STAB-01 |
| 3 | STAB-03 | P1 | Truthful game readiness buttons | Reuse existing visibility writer |
| 4 | STAB-04 | P1 | Safely resolve blocked/migrated game mappings | Per-game review, not global cleanup |
| 5 | STAB-05 | P1 | Persistent Amazon bot Marketplace grant | Exact identity and destinations |
| 6 | STAB-06 | P2 | Get Started becomes an optional guided tour | Working links/support |
| 7 | STAB-07 | P2 | Readable owner log; meaningful order events | Review existing PR #4 |
| Every slice | STAB-08 | P1 gate / P2 consolidation | Repeatable release and post-restart verification | Before every owner update |

STAB-01/02 can share one focused ticket PR. Keep Games, Amazon, tour and owner-log repairs independently reviewable. Do not bundle the whole backlog into a production repair.

## STAB-01 — Repair the entry, not the security check

**Observed:** E1/E2. **Code-confirmed:** both errors occur before ticket creation.

[TicketEntry.create](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/cogs/tickets.py#L39-L49) compares the clicked message/channel with `ticket_entry:<guild>` and `managed_channel:<guild>:need-support`. [SupportOffers.request](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/cogs/tickets.py#L110-L151) checks its own canonical entries before calling the same ticket service. Persistent views are registered in `Tickets.cog_load`. The observed replies rule out a simple missing-handler explanation for these particular clicks.

Electricity uses the `electricity` channel mapping but deliberately retains the `household` partner-message key. Do not blindly rename it. See [Marketplace continuity](PARTNERS.md).

**Diagnostic order:**

1. Verify running commit, bot identity, guild and persistent DB location without rendering secrets.
2. Compare interaction guild/channel/message/author with BOTH settings and the managed-message record. Report which comparison failed.
3. Distinguish missing mapping, obsolete entry, duplicates, replaced bot identity, access denial and wrong runtime DB. None is proven from the screenshots alone.
4. Inspect startup recovery/refresh. [Tickets.reconcile](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/cogs/tickets.py#L160-L177) calls recovery before entry refresh in one try block: a recovery exception can prevent entry refresh. This failure coupling is confirmed in code, not confirmed as the live cause.
5. Inspect partner sync separately. [sync_support_messages](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/services/support_service.py#L452-L503) returns early when any required active partner mapping is unavailable. Another missing board can leave electricity unrefreshed. Verify the live condition before attributing the incident to it.

**Repair contract:** reuse managed-message/upsert/reconciliation services. A read-only diagnostic must not run full setup, ticket recovery that edits access, or partner sync that can resume retirement. After scoped preview/confirmation, rebind only a proven same-bot canonical entry. Preserve customized text/buttons, unrelated pins and ticket histories. Multiple candidates or changed fingerprints require review. Never accept any message merely because its custom ID matches.

An obsolete entry should link to a verified accessible current entry. Without one, provide a safe help route and a short staff reference, not an endless instruction to click the same message. Independent recovery failures must not block healthy boards, but must remain visible in diagnostics.

**Done when:** A01–A05 pass before/after restart; obsolete/lookalike/wrong-guild entries remain rejected; repeated scoped repair creates no duplicates.

## STAB-02 — Complete private-ticket lifecycle

Do not assume the entire backend is broken because the entry is blocked. [ticket_service](../services/ticket_service.py) already owns typed private tickets, limits, durable records, access, recovery and history-preserving closure; reuse it.

Test general support and `ELECTRICITY_REQUEST` separately. Creator and intended staff have access; another ordinary member and external bots do not. Verify Take Ticket, Waiting for User, authorized close, closed read-only history, concurrent clicks and restart recovery without duplicate channels/opening messages.

Check effective rights for actual operations: channel creation/editing, messages, embeds, reads and pinning. Verify current Discord pin permissions rather than assuming Manage Messages covers everything. Missing cached membership is not proof a user departed. If creation succeeds but notification/cache/pinning fails, report partial completion and recover safely rather than creating again.

The electricity button currently creates a private human-assisted comparison request, not an automated tariff-search API. Make that outcome clear without introducing a provider here.

**Done when:** A06–A09 pass with synthetic members/content and a restart. Relevant files: `cogs/tickets.py`, `services/ticket_service.py`, `tests/test_tickets.py`, `tests/test_energy_offers.py`.

## STAB-03 — Green means ready, not merely selectable

**Code-confirmed:** [AdminGameSelection](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/cogs/game_visibility_selector.py#L32-L106) derives green from `selectable` and pending choices, not a usable channel. A missing or pending chat can therefore be green. The owner explicitly rejects that behavior.

The [existing writer/sync check](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/services/game_visibility_service.py#L275-L343) already separates desired policy, PENDING/DONE, channel verification and hidden state. Extract/reuse read-only readiness checks; no new channel registry or mutation path.

| State | Presentation | Condition |
| --- | --- | --- |
| Ready | Green `✅ Game` | Active/visible; valid safe game role; exact text channel under managed Games with intended access; no pending/failed operation |
| Off and absent | Neutral `➕ Game` | Not visible and verified no managed game text channel exists |
| Hidden but retained | Distinct `Hidden · chat retained` | Physical chat exists but ordinary members cannot see it; never claim No channel |
| Incomplete/blocked | `⚠️ Needs setup` / `Needs review` | Missing/stale role/channel, wrong parent, conflicting rights, unavailable verification or pending failure; never green |
| Draft | Explicit `Show planned` / `Hide planned` | Target selection separate from current readiness, not proof of an applied change |
| Applying | Busy/progress | Resolve each result after verification, no premature green |

Use supported button styles plus icons/text, not an invented yellow button style or color alone. Failed verification/cache absence means unknown/review, not absent.

Clicking a visible game with a missing chat must stage **Show / set up**, not Hide or require a double-toggle trick. Ready games can stage Hide. Preserve multi-game Review/Confirm, choices across pages and untouched games. Refresh counts after execution, migration, failure and reopening: visible games, ready chats, blocked/pending games, retained hidden chats.

**Decision D1 — literal absence vs reversible hiding:** the owner said off means no text channel. Existing Hide preserves a physical channel/history. That state cannot truthfully be called absent. Safe default: retain the chat and label it distinctly; say No channel only for actual absence. Physical deletion requires a separate destructive product decision and per-operation history-loss preview. Do not silently redefine the request, and do not delete chats just to make the UI binary. This documentation authorizes no deletion.

**Done when:** A10–A15 pass. Missing/unverified/pending resources are never green. Current versus planned is clear. The new contract supersedes the former tooltip saying green games may still need chats.

## STAB-04 — Resolve blocked games without bypassing safety

E4/E5 show progress and unresolved cases. Additional grants/unavailable chats are review gates, not permission to erase custom access or IDs.

Inspect canonical channel_id, legacy hints, exact current type/existence and references before create/move/rebind. Distinguish 403, 404 and cache misses. Preserve a stored chat after its old category disappears. For conflicting grants, show the exact affected role/member privately and a scoped confirmed choice instead of a generic dead end. Never adopt a channel solely by name.

Audit old Game System Migration versus the new visibility writer: earlier setup blocked extra grants while a separate migration could move chats. A six-chat success message does not prove identical access safeguards ran. Both routes must enforce compatible ownership/rights checks and the hidden policy. Avoid broad refactoring unrelated operations.

A menu opened before migration may display stale counts rather than prove DB corruption. Refresh or provide Reload before diagnosing it as corruption. Sorting must not intentionally reorder unrelated server areas.

**Done when:** A16–A18 pass with clear per-game results, preserved IDs/history and idempotence. No legacy/category deletion is included.

## STAB-05 — Amazon bot access survives GamerHQ repair

**Reported need:** Marketplace access. Exact bot identity, active modules and effective live overwrites are not inspected.

**Code-confirmed gap:** [bot_group_service](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/services/bot_group_service.py#L1-L39) registers Jockie/Pancake/Instant Gaming/DealGecko, not Amazon. [partner repair](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/services/support_service.py#L512-L554) handles known bots but applies read-only repair to Amazon. [guide_overwrites](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/services/onboarding_service.py#L129-L166) sets sending bits from staff/self-bot status; an external-bot send grant can become false. This is a repair hazard, not proof it caused this live failure.

**Integration contract:**

- Resolve exact bot member through the existing verified-ID configuration/settings flow. No name-based trust, all-bots access or token in GamerHQ.
- Initially post only to the managed Amazon text channel. Reuse Marketplace and its stable internal `partners-benefits` alias.
- Basic posting: View Channel, Send Messages, Embed Links. Read Message History only for configured read/edit/reply/recovery behavior; Attach Files only if used. Check effective access, not just one overwrite.
- Parent visibility and child posting are distinct. Do not grant posting to every Marketplace child or access to tickets, Staff, purchases/buyer ranking. Extra destinations require preview.
- No Administrator, Manage Channels/Roles/Webhooks, Mention Everyone or broad message deletion for simple affiliate posting.
- Persist per-feature managed grants that read-only/setup/repair preserves while members stay read-only. Bot grouping is cosmetic, not global access.
- Optional ordinary-message conversion needs a separate check of the Amazon bot's own intents/configuration. Message Content intent and channel ACLs are different. Do not enable external APIs, scraping or purchases here.
- Bot replacement/removal revokes only owned grants after confirmation. Prevent cross-bot duplicate posts/feedback loops.

**Done when:** A19–A22 pass, especially post-repair/restart and negative ticket access. Coordinate private Amazon code changes separately; do not copy private implementation/configuration into this public roadmap.

## STAB-06 — Get Started becomes a real optional server tour

[OnboardingEntry.start](https://github.com/Duy-Phan96/GamerHQ/blob/ffd039f6df274aa559bd3ed4231212c3c8612306/cogs/roles.py#L260-L267) currently calls open_profile. Owner wants a tour of real functions/channels instead. This is a planned UX change, not a demonstrated VPS regression.

Retain persistent entry identity and the managed welcome message. **Start Tour** becomes primary; Profile Settings remains separate/optional. Reuse existing games/profile/LFG flows rather than cloning them.

Proposed short English tour:

1. **Welcome:** Find Games. Find Mates. Play Together; what this community is for.
2. **Choose games:** game roles and relevant chats; open the existing personal selector and return.
3. **Community:** introductions/general with a useful example first message.
4. **Find a group:** real central LFG/event entry; explain Join/Create without creating an actual event from a tour page.
5. **Play together:** explain the current voice/lobby flow, not removed per-game voice channels.
6. **Help and extras:** verified support, optional deals/Marketplace/commands; only enabled accessible functions. Profile details stay optional.
7. **Finish:** clear next action, replay and safe resume.

Previous/Next/Skip/Close with visible progress. No forced gender/age, role selection or notification opt-in. Resolve current accessible links by managed identity, not hard-coded IDs/names. Never expose private staff/ticket links. Omit unavailable extras; do not fabricate working routes. Viewing a page must not create tickets, events or voices.

If resume is persisted, store minimal progress/version using existing storage; no duplicate profile system. Completion is not proof an external action happened. Old welcome controls must work after restart and customized copy must survive a reviewed update.

**Done when:** A23–A26 pass on desktop/mobile for new/existing members, including completing without personal fields. Native Discord Onboarding remains separate future scope.

## STAB-07 — Readable log and meaningful order changes

Existing [PR #4](https://github.com/Duy-Phan96/GamerHQ/pull/4), `fix/owner-changelog-readable-groups`, is open/draft at review; head `90ffbd9311331ec9479c07edb92ddcd9c88fa521`. Re-read its actual code/base/CI before reuse; its old description is not a release result. Do not merge it as a dependency of tickets/Games.

Retain prior requirements: friendly historical names, owner-private details, grouped category deletion/child detach events, restart-safe binding and useful Undo controls only. E8 needs separate classification: absolute position shifts may be collateral. Verify relative sibling order/causal evidence before claiming someone moved an untouched channel. Do not suppress every position change, guess an actor or reverse incidental renumbering unsafely. Keep raw history privately.

**Done when:** A27–A29 pass; genuine moves and concurrent name/permission changes remain visible; exact current tree passes tests.

## STAB-08 — Release gates and broader smoke coverage

Record source/merge commit, exact tested tree/CI, scope and rollback limits before the owner-approved update. Main is behind the feature lineage in the reviewed remote list; never tell the owner to pull main blindly or infer the running version from git alone.

Coordinate updates/backups; preserve the actually running image; verify SQLite backup; validate env syntax without values; use existing preflight before restart. Afterwards verify container commit/health, persistent entries and affected permissions. Image rollback does not reverse Discord ACL/move changes; DB restore can lose newer state. Neither is automatic.

Per slice update status: `Planned → In progress → Offline verified → Ready for owner test → Live verified`, recording blocked/partial outcomes. Prior PR #6 test results are historical, not tests of future fixes or this live state.

After core repairs, smoke-test personal games, admin review/cancel, LFG create/join/leave, current temporary voice, suggestions, role preferences, verified feeds, managed pins and staff/private access. No global reset/cleanup. Separate Dev guild/bot/DB is future infrastructure, not provisioned here.

## Next executable slice

Start **STAB-01** with scoped read-only entry-binding diagnostics and synthetic reproduction, then the smallest verified mapping/recovery fix. Preserve the guards. Complete STAB-02 before advertising support as repaired.

Tour, truthful game buttons and Amazon grant are explicitly queued here, not shipped. No VPS pull/restart or manual channel creation is needed for this documentation branch.
