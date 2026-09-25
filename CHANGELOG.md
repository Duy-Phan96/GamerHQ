# GamerHQ Changelog

## Internal maintenance and reliability

- Batch managed channel order settings without changing adopted layout, resource IDs or permissions; reuse the shared HTTPS link policy and channel alias normalization.
- Recheck current administrator membership for role sessions and Game Library confirmations, including after their initial Discord acknowledgement; preserve authorized flows and existing controls.
- Close remaining bot resources even when earlier shutdown cleanup fails. Declare the existing locked aiohttp version directly; retain deployment, feature flags and public behavior.
- Add offline regression coverage for order planning, settings batches, revoked sessions, public URLs and shutdown failures.


## Marketplace Electricity service

- Reuse the existing partner category and household-check channel as 🛒 MARKETPLACE and 🇩🇪・electricity; preserve IDs, history, custom pins and unrelated integrations.
- Provide English electricity-only copy and private `ELECTRICITY_REQUEST` tickets; retain historical ticket types and disable old entry actions.
- Update navigation, editor, setup/repair and read-only health. Explicit Repair migrates persisted channel/adoption mappings with conflict checks and retry safety; custom legacy pins require owner review.


## [Unreleased] - 2026-09-23

- Polish Profile Settings: personal onboarding/correction is Gender → Age → Review → Save only; keep platform/interests quick toggles, hide inactive playstyles, remove language selection for the English-language server, and explicitly retire the owned About You pin/legacy language mappings during Repair without removing existing roles or memberships.

- Refine Profile Settings into grouped About You, Gaming Setup and Interests & Notifications; reuse existing message IDs, move Suggest Role to the last board, and add a preselected member-bound Gender → Age → Language → Platform → Playstyle → Interests → Review → Save flow. Preserve retired playstyles/game roles, support Diverse and no-visible-role gender privacy, and keep quick controls independent.

- Add confirmed owner/admin GoCDKeys batch import: strict supplied partner URLs, conservative editable slug titles, paginated preview, paced individual posts and durable normalized-URL deduplication through existing curated delivery records. Preserve correct referrals and reject conflicts; automated lookup remains disabled.

- Add confirmed owner/admin curated deals with strict manual links/prices and durable delivery claims. Disable unsupported automatic GoCDKeys scraping/backfill despite legacy opt-in; retain manual referral links.
- Repair legacy Instant Gaming identity resolution and uncached direct sync; diagnose effective Affiliate Stats parent/child access without granting private access to Gaming Bots. Preserve unknown synced children for manual review.
- Paginate the managed Staff Command Guide, retaining the original page ID and adding `/deals` without exceeding Discord message limits.

- Add owner/admin `/deals backfill` with bounded read-only preview, confirmed shared-provider processing and durable live/backfill duplicate prevention. Repair explicit Instant Gaming partner-category discovery denies and report its/GamerHQ gaming-deals access without widening private access.

- Prepare hidden Twitch Streamer Hub beta: approved-role Device OAuth, outbound EventSub, durable session deduplication, confirmed disconnect, private setup/health and default-off role selection; retain inactive legacy profile data.
- Fix embed-only gaming-deals comparisons blocked by price detection; trust configured application identities, update/delete only mapped GamerHQ companions, and report posting permission/intent readiness.
- Reuse GoCDKeys for trusted paid Instant Gaming/DealGecko posts; skip free/unknown offers, update owned companions on price edits, and repair scoped Gaming Bots/DealGecko paid-channel access.

- Fix third-party bot grouping with central public IDs, repair-only exact-member fetch fallback, per-bot outcomes and read-only membership checks; preserve Instant Gaming identity and scoped private access.

- Add optional GoCDKeys comparison replies scoped to managed Instant Gaming deals, validated product pages, persistent duplicate protection and read-only health status.
- Preserve News/Deals and insert gift-emoji Free Games for DealGecko; migrate existing private IG IDs to AFFILIATE STATS purchases/buyer-ranking. Owner Repair ensures hoisted, least-privilege Music Bots/Gaming Bots groups using verified bot IDs, with read-only health checks and manual external configuration.

- Separate optional gender/age onboarding from game selection and five independent settings boards. Add managed Gaming News/Deals opt-ins and per-game LFG notification roles, persistent controls, narrow migration and read-only health checks; retain existing IDs and retire global playstyle/LFG preferences without deleting member roles.

- Detect persisted support/partner/Instant Gaming channel drift with private owner/admin approval buttons, exact self-change suppression and four-second coalescing. Persist decisions and deletion tombstones, immediately repair private/bot access drift, and report unresolved records in read-only health. Public posting-policy adoption is limited to the managed @everyone Send Messages bit.

- Default support channel name is 💜・support-gamerhq, retaining its stored ID. Add explicit owner/admin `/server adopt` previews and confirmation for selected public board names, parents and order; store overrides in existing settings and respect them in repair/sync/health. Permissions remain outside adoption.

- Explain direct support and partner/deal support in the existing Support GamerHQ pin; safely retire the managed direct-support channel during owner Repair without recreating it.

- Extend Instant Gaming to four persisted feeds: news/deals at the top of the existing PARTNERS & BENEFITS category, followed by Amazon, AI Tools and Haushaltscheck and private purchases/buyer ranking in existing STAFF. Reuse the partner deals pin/affiliate button, respect Staff-category posting policy, report per-channel health/duplicates and log missing bot configuration without blocking setup.

- Prepare persisted public gaming-news and staff-only ig-purchases destinations with managed pins, the existing INSTANT_GAMING_BOT_ID configuration, targeted permission repair, setup inventory and health checks. See `docs/INSTANT_GAMING.md` for configuration and acceptance.

- Refine Partners & Benefits defaults to lead with useful offers and neutral disclosures. Explicit owner Repair safely retires recorded legacy finanzberatung after full dependency/content/thread checks; health reports repair availability or exact review reasons. Preserve custom messages, IDs, household privacy and historical records.

All notable GamerHQ Bot changes are tracked here from the stable-release workflow onward.

## [Unreleased] - 2026-09-22

- Polish partner defaults in English while keeping Haushaltscheck German. Simplify Amazon/Gaming Deals/PixVerse copy and add concise disclosures beside unchanged affiliate links; retain manual Ctrl + D guidance and remove public integration wording. Preserve custom edits and all ticket behavior. Retire obsolete active legacy channel/message mappings while retaining historical IDs for manual review.

- Replace standalone energy, finance and course public flows with Haushaltscheck and private `HOUSEHOLD_CHECK_REQUEST` tickets. Preserve historical tickets and custom/manual content; journal legacy cleanup for retry and report retained channels for manual review. Update five-channel navigation, editor actions, setup and health.
- Prepare optional official Instant Gaming bot posting in gaming-deals with a verified bot ID and scoped permissions; document marketing/release configuration with purchase notifications and buyer ranking disabled. No scraper or live integration activation.
- Complete deployment/restore documentation and self-contained release acceptance checks; fix the health test's POSIX process probe isolation on Windows.

- Prepare AlmaLinux deployment with locked container dependencies, external persistent storage, local health checks, verified backups/retention, offline migration preflight, owner-run updates and a systemd backup timer. Align deployment and rollback instructions with `/opt/gamerhq/{app,data,backups}`; host image build and live Discord acceptance remain owner gates.
- Replace the generic `/server pinned-messages` flow with an owner/admin editor for registered public boards, including both Strom & Gas messages. Add Markdown drafts, structured link/allowlisted action buttons, inert previews, in-place confirmed saves and confirmed Reset to Default.
- Persist custom content/buttons through restart and repair using the existing managed-message helper. Add version conflicts, shared edit/refresh locks, metadata/hash audits, pending-delivery recovery and read-only registry health checks.
- Use the friendly Support GamerHQ affiliate disclosure and document editor permissions, supported boards, recovery and owner acceptance steps.

## [Unreleased] - 2026-09-12

- Added ID-based Music Bots role configuration and least-privilege access sync in
  owner setup, plus automatic access for game areas and gaming temporary voices.
- Added `/server cleanup-game-areas`: private preview, one-area confirmation,
  fresh dependency/topology checks, resource coordination and per-deletion logs.
  Game records/roles remain intact; setup never invokes deletion.
- Added 20 offline music/cleanup regressions (60 total passing). See
  `MUSIC_CLEANUP_REPORT.md` for scope, safety rules and owner acceptance steps.

- Focused owner setup update: migrate old welcome to COMMUNITY/newbies, establish
  a read-only START HERE/welcome, and standardize community-commands as bot-commands.
- Replace the long member command pages with one concise command/music guide;
  use the registered `/game` and `/lfg` commands and verified music command names.
- Stop regenerating Introductions guides; remove only identifiable obsolete bot
  onboarding pins from introductions/newbies. Preserve conversation and unrelated pins.
- Add persistent channel identity, guarded pin recovery, posting permission changes
  and ten offline migration/permission/idempotency tests. See `ONBOARDING_REPORT.md`.

## [Unreleased] - 2026-09-11

- Added active lobby dashboard cards, host/member action panels, post-creation invitations,
  safe editing, participant removal and host-approved time proposals.
- Preserved private event channels, public/per-game discovery posts and existing share flows.
- Made final joins transactional and invitation-aware; retained occupied voice and failed
  cleanup mappings; show final event state before delayed cleanup.
- Added four event columns and the indexed `lfg_time_proposals` table through additive startup
  migrations. Back up the runtime DB before deployment; no live migration was run here.
- Added offline permission, migration, concurrency, dashboard and voice regressions.
  See `ACTIVE_LOBBY_REPORT.md` for acceptance steps and limitations. Not yet live-tested.

## [1.0.0-beta.1-rc4] - 2026-09-04

### Games
- `/game-admin rename` accepts an optional `emoji` argument.
- Omitting or leaving `emoji` empty preserves the current game emoji.
- Emoji-only changes are supported by keeping the existing game name.
- Confirmed changes update and roll back the DB/catalog, seed entry, linked role, linked category and Choose Your Games consistently.

### Hosting and workflow
- Added a non-root Docker image and Compose service with persistent database and backup mounts.
- Added read-only production preflight and verified SQLite backup commands.
- Pinned direct Python dependency versions used by the tested release candidate.
- Replaced legacy overwrite-in-place deployment notes with a Git/tag-based Linux VPS deployment and rollback workflow.

## [1.0.0-beta.1-rc3] - 2026-09-04

### Stability
- Fixed an event-join regression where `Add Game & Join` refreshed Choose Your Games without component views and removed every public game button.
- Role assignment during event join no longer refreshes the selector because its content is unchanged.
- Selector refresh now rejects missing category/intro views before reading or editing Discord messages.
- Startup safely restores the current DB-driven game buttons using the required persistent views.

## [1.0.0-beta.1-rc2] - 2026-09-03

### Stability
- Fixed both event-creation entry points: the builder registered six buttons in row 4, exceeding Discord's five-button row limit before `_rebuild()` could run. Removed the unused builder Cancel button; cancellation remains in the preview.
- Game create, add-area preview and area confirmation now acknowledge interactions before database access or structure scans.
- Removed the automatic V27 channel migration from startup. Legacy game memes links are reported for staff review without deleting channels or clearing DB links.

### Performance
- Area creation no longer refreshes Choose Your Games, since selector membership is unchanged.
- Selector refresh reuses messages loaded from history and skips edits when content and components already match. Stored-ID fetch remains available for older messages.
- Added an offline profiler using a read-only snapshot of the runtime DB and mocked Discord calls. No production benchmark writes or Discord login.

### Validation and release status
- Added offline regression tests for both event entry points, builder/preview navigation, early acknowledgements and selector refresh.
- No schema change; runtime DB and `.env` remain byte-identical. Code-only rollback snapshot is stored locally under `backups/` and is excluded from releases.
- Remains a release candidate pending Discord smoke tests and hosting acceptance. See `STABILITY_REPORT.md` for measurements, remaining risks and rollback instructions.

## [1.0.0-beta.1-rc1] - 2026-09-03

### Stability
- Introduced the first release-candidate baseline for the new versioned deployment workflow.
- `Create Event` now acknowledges Discord immediately before loading the event builder, preventing interaction timeouts caused by slow work before the first response.
- `/lfg create` uses the same acknowledged builder flow.
- Final event creation now defers before creating private channels, posting event messages and sending invites.
- Event builder errors are logged and return a user-visible error instead of Discord's generic "application did not respond" message.

### Games
- Game Library, game-role visibility and dedicated Discord areas remain separate states.
- Existing custom games can be recovered from matching Discord resources without recreating them.
- Dedicated multiplayer areas use `chat`, `looking-for-group` and `create-voice`.
- Solo areas use `chat` and `create-voice`.
- Legacy per-game `memes` channels are no longer part of the desired game-area structure.

### Events
- Public and private LFG events are supported.
- Public events expose a shareable Discord event-post link.
- Private events support host-managed invite access, private event channels and participant-scoped voice access.
- Discord-only private sharing remains a Beta flow until a future GamerHQ web endpoint provides one-click event links.

### Community
- `community-commands`, `streamer-commands` and `mod-commands` are separate automatically refreshed command references.
- `tournaments` and `giveaways` replace the old community memes/clips channels with Coming Soon messaging.

### Deployment safety
- Runtime database files are not shipped in releases.
- `.env` is not shipped in releases.
- `GAMERHQ_DB_PATH` can point to a persistent database outside the application/release directory.


## Core structure and private suggestions — 2026-09-13

- Preserve channel IDs while moving LFG to START HERE and tournament/giveaway channels to EVENTS.
- Add one central guide and read-only suggestion entrypoint; shorten the bot-command pin.
- Persist private staff suggestions and restart-safe status buttons; route game/role suggestions privately.
- Preserve existing lobby, selector, voice and music behavior; exclude staff/core boards from music posting grants.


## Voice owner controls and Area management — 2026-09-13

- Rename/reuse 📘・guide and publish concise member instructions.
- Add persisted-owner `/voice manage` controls without direct creator management grants.
- Add paginated `/area manage` batches with shared safety checks and single-use removal confirmation.
- Route legacy area removal through the same checks; preserve games, roles and LFG availability.


## Acceptance preparation — 2026-09-14

- Add read-only health classifications/details and setup repair previews.
- Recover core guides by persisted identity; preserve unknown resources.
- Deduplicate rapid suggestions and validate staff status transitions.
- Harden expired LFG actions, scheduler isolation, dashboard authors and departed participant cleanup.
- Reduce implicit startup migrations; retain ambiguous Voice records for review and ignore unchanged-channel events.
- Add consistent private failure handling, audit logs and generated command inventory.


## Affiliate support board — 2026-09-16

- Add owner-repaired, read-only Support GamerHQ category/channel with stable resource IDs.
- Maintain one canonical affiliate pin for Instant Gaming, PixVerse and Amazon, plus a short guide reference.
- Include support in setup inventory/health and add eight offline regressions; no payment or donation feature.

## Private support tickets — 2026-09-16

- Add separate Need Support and Support GamerHQ entries in START HERE; move the existing affiliate channel without changing its ID or deleting the old category.
- Add private ticket forms, current Staff role access, assignment, waiting state and confirmed closure with readable history.
- Persist ticket metadata/audit, limit open tickets, serialize actions and recover canonical controls after restart without blindly recreating channels.
- Reconcile creator departure and Staff permission changes, including access to historical ticket logs.
- Add owner setup/health diagnostics, capacity safeguards and offline ticket regressions. No automatic ticket deletion, donations or live migration.

## Public repository preparation — 2026-09-17

- Expand Git/Docker privacy exclusions and add redacted working-tree/index/history auditing.
- Document portable local setup, actual command inventory, architecture, security and contribution guidelines; leave licensing to the owner.
- Add an isolated offline test entrypoint and credential-free test-only CI workflow.
- Validate essential startup settings without echoing values; resolve relative DB paths from the repository and create runtime directories only on database use.
- Copy only explicit application sources into Docker; keep private data mounts separate from the public seed catalog.
- Retain pinned dependencies and existing product behavior; no deployment, commit, push or live migration.

## Support navigation polish — 2026-09-19

- Put each managed partner channel mention directly in its Support GamerHQ list item; remove the repeated footer and simplify referral disclosure.
- Omit missing/deleted mappings from navigation and flag them in health for owner setup repair; preserve canonical messages and unrelated pins.
- Update the README server tree, command count, setup/architecture references and contribution documentation policy.
