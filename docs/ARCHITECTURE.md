# Architecture

Python/discord.py interaction layer (`cogs/`) → services (`services/`) → SQLite persistence (`database/db.py`) → managed Discord channels, roles and messages.

- `bot.py`: intents, extensions, guild command synchronization and restart reconciliation. Importing it does not log in.
- `config.py`: private environment loading and portable runtime path configuration. Essential settings are validated before login.
- `database/db.py`: schema, additive initialization/migrations, transactions and application state. `data/games_seed.json` is a public game catalog with no users or server resource IDs.
- `cogs/`: commands, buttons, modals and event listeners; authorization is rechecked on actions.
- `services/`: business rules, permissions, resource identity, message rendering and safe repair.
- `tools/`: offline tests/publication audit and explicitly invoked operational utilities.
- `tests/`: synthetic Discord fakes and temporary SQLite. No live data fixtures.

## Source of truth

Git is the source of truth for code. Private SQLite is the source of truth for live application state; Discord is its integration/rendered state. Resource IDs in settings and domain tables preserve identity through rename/move/restart. Reconciliation does not turn every unknown Discord resource into a managed resource. Ambiguous mappings require owner review.

A **Game** is a library/selection/role record. A **Game Area** is an optional Discord category with linked channels. Removing an area does not inherently remove the game or its role/selection. Explicit destructive maintenance retains its existing confirmation rules.

LFG persists events, participants, private invitations/share codes and time proposals. Dashboard messages render that state. Temporary Voice stores owner/room/game mappings; owner controls use bot-managed permissions and empty-room cleanup. Streamer voice/resources have their own lifecycle.

Support tickets persist metadata, creator, assignment, status and private channel/message IDs. Creator/Staff access is ticket-specific. Closure preserves history and locks creator posting; no automatic transcript export/deletion. Suggestions enter a private Staff inbox and persist review state. Runtime ticket text, user activity and invite codes belong in private storage, never Git.

Music Bots integration configures an existing dedicated role for external bots. GamerHQ does not play music or collect third-party music credentials. Affiliate URLs and their disclosure are intentionally public content in `services/support_service.py`.

## Operations boundaries

Owner setup previews/repairs established server structure; health is read-only. Startup does synchronize commands and reconcile known resources, so a real start is not an offline check. Deploy one instance, separate code from runtime data and back up SQLite. Docker keeps the public seed under `/app/data` and mounts private SQLite under `/app/runtime/data` so the seed remains visible.

## Support overview navigation

START HERE / support-gamerhq is a short directory for five separate PARTNERS & BENEFITS channels. Each bullet renders the mention from its persisted `managed_channel:<guild>:<name>` mapping; the category name is plain bold text. Synchronization does not use name-only lookalikes for navigation. Missing destinations are omitted, health flags missing mappings, and owner repair handles creation/adoption. The canonical intro message is edited through the shared managed-message helper, with no duplicate navigation footer or additional overview message.

## Managed message customization

`services/server_service.py::upsert_fixed_message` remains the only fixed-message creation/refresh abstraction. Its explicit editable-board registry in `services/managed_message_service.py` layers customization onto existing setting keys and message IDs. `cogs/managed_messages.py` replaces the former generic pin editor under `/server pinned-messages`; it does not create arbitrary pins.

SQLite `managed_message_content` stores each board's latest generated defaults, canonical Markdown, ordered structured buttons, identity, content hash, customization flag, delivery state and version. `managed_message_audit` records actor/channel/key, timestamp, edit/reset, content/buttons changed flags and before/after hashes, without bodies or URLs. Treat both tables as private runtime data.

Refresh and editor saves share per-key asyncio locks within the single supported bot process. Draft versions detect changes since opening; UI generations invalidate previous menus/modals. Saves validate and recheck current owner/admin access and mapped bot-owned pinned-message fingerprints, persist canonical intent/audit, then edit that exact Discord message. Failed/uncertain HTTP leaves pending delivery for health and normal repair to reconcile using the old or intended content fingerprint. The editor never sends a replacement pin. Normal repair can recover an actually deleted message; customized retired partner pins are retained for manual review.

Normal startup/setup/sync preserves customized bodies and buttons while recording new defaults for a later confirmed reset. Allowlisted action buttons keep existing persistent custom IDs and callbacks, restricted to their canonical board so ticket-source checks still apply after restart. Link buttons accept public HTTPS URLs and reject credential-bearing configuration. Preview components are inert; live mentions never ping on editor saves. Read-only health checks mappings, duplicate identities, fingerprints, pin state, configuration/action allowlists and pending delivery; changed custom headings are valid.

## Haushaltscheck migration and external deal posting

Owner repair journals recorded legacy energy/course/finance message IDs before reusing a channel for Haushaltscheck. Once all replacement pins exist, it retires known default messages; uncertain/customized content and all historical tickets remain. Old ticket types are read-only compatibility types for existing ticket lifecycle actions, never accepted for creation. Retired managed registry rows remain in SQLite but are excluded from active editing/health. Completed migrations retire obsolete active channel/message settings into historical `retired_partner_channel` / `retired_partner_message` identities, retaining legacy-channel manual-review tracking. The new request type uses the existing ticket service, per-type limit, privacy, audit and persistent buttons.

Optional `INSTANT_GAMING_BOT_ID` identifies an externally installed bot member. Owner repair grants only that member posting/embed/attachment access in gaming-deals. Other boards remain read-only. Marketing configuration and affiliate attribution belong to the official external bot; GamerHQ does not scrape, publish deals or enable purchase notifications.

## Production operations

Compose reads `/opt/gamerhq/.env` and mounts sibling data/backups outside the checkout. A local gateway/event-loop heartbeat supplies container health with no exposed port. The systemd timer invokes a one-off backup container with an update/backup lock; verified daily snapshots alone are eligible for explicit 14-snapshot retention. Production preflight rehearses schema initialization on disposable SQLite. See [deployment](../DEPLOY.md) and [rollback](../ROLLBACK.md).

Legacy finanzberatung retirement is restricted to explicit owner setup Repair after replacement pins and mapping migration complete. `legacy_finance_service` shares read-only safety inspection with health: persisted identity, channel name/location, all stored resource dependencies, managed fingerprints, full message history, active/public/private archived threads and inspection permissions. Safe channels are REPAIRABLE; uncertainty is MANUAL_REVIEW with an exact reason. Sync/startup never call finance deletion. Historical tickets and audit/retired records remain stored. See [partner rollout and benefits-first copy](PARTNERS.md).
