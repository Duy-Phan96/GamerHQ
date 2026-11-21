# GamerHQ Community — functionality, launch and growth roadmap

_Owner direction: 2026-11-21. Status: actionable plan, not a claim that all checks passed._

## Product goal and priority

**Ship a dependable gaming community people can join and enjoy, then promote it.** The existing Production server is substantially configured. Focus on its remaining Games/channel gap, real member journeys, fixes and operational release acceptance. Do not introduce a new Discord test guild or re-architect multi-guild admission just to complete this roadmap.

GamerHQ's member promise: **Find Games. Find Mates. Play Together.**

## Separate workstreams, shared contracts

| Workstream | Goal and current priority | Owns / does not own |
| --- | --- | --- |
| **GamerHQ Community (P0)** | Finish the Production server, fix bugs, improve member UX, prepare launch and ongoing operations | Owns Gaming Community flows and Host integration, not private implementations of external Skills |
| **Skills / Runtime (P1, separate)** | Reliable independently installable/upgradable Skills and SDK contracts | Separate package lifecycle; GamerHQ consumes reviewed pinned versions |
| **Server templates (P1, separate)** | Optional templates, first installation, Preview/Apply/Repair, idempotence and permission safety | Tested on Servertest; does not modify Production without owner review |
| **TikTok growth/content (P1 once join path passes)** | Create short-form video content with clear invite/onboarding funnel and measurable outcomes | Content scripts, capture/edit/publish plan; owner approval required for publishing |
| **Website (future)** | Public portal / management experience for server owners and gaming communities | Separate web project; no production web feature commitment here |
| **Mobile app (future)** | Companion experience if member demand and contracts justify it | Separate future project; not a dependency for current launch |

Cross-project requests follow the [repository ownership and handoff contract](REPOSITORY_OWNERSHIP_AND_HANDOFFS.md) and existing GitHub handoff queue. Do not merge independent roadmaps into one mega-PR or central context thread.

## Existing server/environment contract

| Environment | Purpose | Allowed approach |
| --- | --- | --- |
| **Production GamerHQ** | Real community, release destination and only server we actively promote | Read-only initial inventory, safe existing resource preservation, release candidates accepted on Staging, backups/rollback and explicit owner-approved deployment |
| **GamerHQ Staging** | Functional **production-like mirror** for testing all server updates and bug fixes | Match the **desired** Production layout, roles, game/channel policies and feature configuration through redacted fixtures and separately owned IDs; verify drift, permissions and behaviors before accepting |
| **Servertest** | Fresh installs, templates, independent Skill/setup lifecycle, partial states, permission denials and idempotence | Intentionally differs from Production; isolate test data; run mutation tests only after explicit review/authorization |

A mirror means **functional/configuration parity**, not identical Discord snowflake IDs, copied live databases, transcripts, member records, production credentials or private roles. Do not destroy Staging's test state merely to resemble Production. A repair/preview must show differences and request consent before writes.

## Delivery roadmap — sequenced

### P0.1 — Baseline functional inventory and release scope

**Outcome:** one verified, dated matrix comparing Production vs GamerHQ Staging, with actual resource IDs/permissions, deployed Host/Skill pins, feature status and owner-visible discrepancies.

- Use authenticated **read-only** bot/Host inventory and existing `/server health`, Games administration, setup/permissions and release-snapshot tools; prefer automation and redacted evidence to Computer Use.
- Capture desired vs observed categories, managed messages, game roles, games visible/hidden, canonical game channels, permissions, linked resource IDs, LFG/event/voice destinations, Staff/Support visibility and unresolved setup journals.
- Record each feature as `PASS`, `PARTIAL`, `BUG`, `NOT_CONFIGURED` or `UNVERIFIED` with issue links, owner, severity and reproduction. Never infer a Production state from Staging alone.
- Inventory existing GitHub Issues and the `[HANDOFF QUEUE]` before opening duplicates.

**Gate:** baseline evidence published to a normal development PR/issue, no production writes and no accidentally copied secrets or member data.

### P0.2 — Games and associated channels (first functional priority)

**Outcome:** players can select the intended games, receive only their game roles, and find the appropriate role-gated text channels without duplicate resources or lost messages.

1. Audit visible/hidden catalog games, game-role mapping, existing Games category, stored channel IDs, linked older channels and missing/blocked channels **on both Production and Staging, read-only first**.
2. Decide the owner-approved *initial* set of games/channels from observed demand and desired launch catalog. Do **not** automatically create channels for every seed game; role-only games are valid.
3. Reuse existing `/server manage → Games → Manage Visible Games` / `Set up Games Channels` / Show Game flows. Display clear per-game `Ready / Not set up / History kept / Review` states with Preview → Confirm. Use `docs/GAME_SYSTEM.md` for current contract.
4. Verify that Show creates/reuses a **single shared Games category and one channel per confirmed game**; Hide preserves history and IDs; existing chats are moved/relinked only through reviewed confirmation.
5. Test permissions with an ordinary member, other-game-role member, owner/staff and bot. Selection does not imply notification opt-in. Repeated Show/Hide and restart do not create duplicate categories, channels, roles or messages.
6. Validate on GamerHQ Staging, then propose **separate, owner-approved Production resource setup/release** with exact preview, backups where state changes, and rollback/repair path.

**Gate:** approved initial game list; no stale/ambiguous mappings; verified role gates, member UX, repeatability and retained history. Do not claim the Production channels exist until independently checked.

### P0.3 — Core member journey, bug fixing and usability

**Outcome:** a new player can join → read welcome/rules → set profile → select games → find/create LFG/event → enter voice → receive support, without needing an admin.

- Run the established [core beta acceptance](CORE_BETA_ACCEPTANCE.md) by release candidate, not by an unfrozen `develop` head.
- Prioritize broken slash commands/buttons, stale Discord interactions, missing permission explanations, duplicate managed messages, confusing setup navigation and saved-state/restart errors.
- Check Event Manager per-guild lifecycle explicitly: **primary Staging installed but disabled vs Servertest available but not installed** as last observed. Do not interpret package availability as installed/enabled or turn on Skills implicitly.
- Use focused issue/PR batches with screenshots only when voluntarily provided; automated APIs/fixtures first. Existing user data/channels survive all fixes.
- Recheck game catalog, LFG public/private joins, event creation/recurrence, voice ownership and ticket privacy after relevant changes.

**Gate:** no P0/P1 blockers in the intended member journey; representative member, moderator and owner acceptance recorded.

### P0.4 — Release, operations and Production launch gate

**Outcome:** a frozen, verifiable release candidate can be promoted to the real GamerHQ server safely.

- Validate both Python CI versions, required `develop` checks, exact immutable Host/Skill versions, configuration/secret capability preflight and migration rehearsal on **copies**, not Production DB.
- Verify Staging parity and candidate feature acceptance, restart/idempotence, scheduler, rate limits, health/logging, least privilege and actionable error reporting.
- Fresh verified backups and independent restore test, rollback plan, production permission/storage checks and release snapshot.
- Explicit owner approval for Production deployment, then scoped smoke/observability; revert or hold on any failed gate.
- Readiness does not imply Production is already running the candidate.

**Gate:** `ACCEPTED FOR DEPLOYMENT` only for an exact SHA/version after evidence and owner consent; use [Server Release Snapshot](SERVER_RELEASE_SNAPSHOT.md) and [Production operations](../PRODUCTION_OPERATIONS.md).

### P1 — Discord server community polish and promotion funnel

**Outcome:** the Production server is welcoming and valuable before outside traffic arrives.

- Validate welcome/rules, role selection, Games discovery, LFG onboarding, announcements, community/support moderation, discoverable channels and sensible empty-state guidance.
- Write a concise positioning message and non-spammy invitation flow. Check onboarding conversion with aggregate measurements where permitted; avoid unnecessary personal tracking.
- Review product copy, permanent invite management, moderation guidelines, support coverage and community-events cadence.
- Promote only when the first-session member journey and moderation are ready.

### P1 — TikTok content and acquisition (separate growth project)

**Outcome:** reproducible original short videos lead interested gamers to a functioning GamerHQ community.

- Content pillars: quick LFG/game-finding demonstrations, member-friendly Games selector walkthrough, real community events (with participants' permission), gaming tips/news with proper rights and brief behind-the-scenes development clips.
- Prepare a **two-week pilot editorial calendar** as a separate content work item, e.g. 3–4 posts/week; quantity is a proposed experiment, not an existing cadence.
- For each video: hook (first seconds) → one clear community benefit → brief screen/demo or original gameplay → call to join GamerHQ Discord. Do not promise prizes or capabilities not launched.
- Define capture/consent, copyright/music policy, captions/subtitles, platform-compliant link placement, editing template, publishing checklist and owner approval. Do not automatically upload content or collect member data.
- Review aggregate performance weekly: views, completed watches, profile/link actions, valid joins and active participation. Optimize actual member activation, not just clicks.
- Coordinate cross-posts or website landing pages later under their own repo/product owners.

**Gate:** first member journey is functional; server moderation and join route reviewed; content assets are original/authorized.

## Known work and non-goals

- **Servertest** was observed with ten pending Gaming Community resources (STAFF and SUPPORT TICKETS categories plus eight private staff channels). This is an incomplete test template, not proof the Production community is broken; apply only in a separately approved template test.
- Two-guild Staging isolation and read-only previews have bounded acceptance. Live callback registry, full member-role audit, mutation-path tests, fresh OS/backup attestations and Production-readiness evidence require additional checks.
- Do **not** add a fourth guild or another test environment; no bulk recreation of Production channels, automatic Game-channel creation, public tenant onboarding, unreviewed Skill installs, or immediate website/app launch.
- Long-term Skills, templates, web and app are separate products/tracks, not blockers for completing the current GamerHQ server.
- No Computer Use/owner desktop control by automation unless explicitly requested.

## Working method and acceptance evidence

Work in priority order; close real bugs before new capabilities. For each feature create one narrowly scoped issue/PR on `develop`, show baseline → desired behavior → tests → Staging acceptance → Production impact → exact next owner action. Keep AI/Codex context per project boundary; link cross-repo dependencies through durable handoffs. Do not treat a passing test or merge as permission to deploy.

**Immediate next engineering work:** P0.1 read-only Production-vs-Staging functional/game/channel inventory, then P0.2 targeted game/channel corrections. The roadmap is not approval to perform live writes.
