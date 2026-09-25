# Architecture

This map describes the current working tree, not a deployed release.
[Development workflow](DEVELOPMENT_WORKFLOW.md) routes validation; read only the
feature references needed for the task.

## Layers and ownership

| Area | Responsibility / extension point |
| --- | --- |
| bot.py | Bot construction, intents, extension loading, guild command sync and on_ready reconciliation |
| config.py | Environment/.env loading, typed IDs, startup validation and DB/seed paths |
| cogs/ | Slash commands, buttons, modals, event listeners, scheduling and authorization at interaction boundaries |
| services/ | Feature rules, permissions, identity resolution, rendering and Discord/DB coordination |
| database/db.py | SQLite schema, additive initialization/migrations and domain data-access functions |
| data/games_seed.json | Public seed catalog; no live server IDs, members or private state |
| tools/ | Offline runner/audit, verified backups, migration preflight and container heartbeat |
| tests/ | unittest/IsolatedAsyncioTestCase tests run through pytest or tools.test with synthetic Discord and temporary SQLite |

There is no separate repository layer, ORM or universal domain-model package.
Rows are commonly dictionaries/sqlite3.Row; a few modules use dataclasses.
db.connect is a context manager that opens SQLite, commits on success and closes.
Several services and cogs contain direct SQL and Discord effects: the layers are
an orientation, not an enforced purity boundary. Extend the existing owner before
introducing abstractions.

## Startup and bootstrap

1. config loads the repo .env with process-environment precedence; DB_PATH is
   relative to BASE_DIR when configured relatively, otherwise the legacy
   project-local gamerhq.db fallback. Deployment overrides it outside source.
2. The __main__ path validates token/guild before bot.run. Import is not a login,
   but config import still reads local environment unless tests disable it.
3. setup_hook initializes SQLite and seeds the catalog, loads games/voice/area/
   server/roles/suggestions/tickets/LFG/streamer extensions and persistent views.
   It synchronizes guild commands and clears legacy global commands.
4. In a container, a local heartbeat reports event-loop/gateway readiness.
5. on_ready cleans empty tracked voice and refreshes known guides/selectors/
   managed boards. It can run again after reconnect. Startup is not read-only
   and must never be treated as a smoke test against production.

The supported deployment is one bot instance per guild/database. Per-key/event
asyncio locks are process-local; they are not a distributed coordination system.

## Feature map

| Feature | Start reading | Contract / tests |
| --- | --- | --- |
| Server structure | server_setup_service → onboarding_service → community_structure_service | [Structure](SERVER_STRUCTURE.md); test_onboarding, test_community_structure |
| Read-only diagnostics | health_service.scan; cogs/health.py | test_acceptance_health; health must not repair |
| Fixed pins/editor | server_service.upsert_fixed_message + managed_message_service; cogs/managed_messages.py | [Managed messages](MANAGED_MESSAGES.md); test_managed_messages |
| Games/areas | game_service, area_management_service, game_area_safety/cleanup; cogs/games.py and area.py | [Games](GAME_SYSTEM.md); test_voice_area, test_music_cleanup |
| LFG | lobby_service, lobby_dashboard, lfg_service; cogs/lfg.py and lobby_management.py | [LFG](LFG_EVENTS.md); test_lobby_management, test_stability |
| Temporary voice | temp_voice_service; cogs/voice.py and voice_controls.py | [Permissions](PERMISSIONS.md); test_voice_area |
| Tickets/electricity requests | ticket_service; cogs/tickets.py | test_tickets, test_energy_offers; private creator/Staff access and retained closed history |
| Suggestions | cogs/suggestions.py + community_structure_service | test_community_structure; private Staff delivery/review state |
| Partner boards/migration | support_service and legacy_finance_service | [Partners](PARTNERS.md); test_support |
| Instant Gaming | instant_gaming_service + existing partner deals pin | [IG](INSTANT_GAMING.md); test_instant_gaming |
| Music / streamer | music_bot_service; cogs/streamer.py | [Integrations](INTEGRATIONS.md); test_music_cleanup |

Service paths are under services/, cog paths under cogs/, and named tests under
tests/ with .py extensions. [Commands](COMMANDS.md) documents the public entry points.

## State and resource management

Git owns code; runtime SQLite/config owns persisted application state. Discord
is the external resource/rendering surface. Resource IDs live in settings and
feature tables; name fallback policies differ by feature. See
[identity/repair contracts](SERVER_STRUCTURE.md), including the legacy exceptions.

A game record/role is independent of an optional Game Area. LFG cards render
transactional event/member state. Ticket storage holds metadata/state/private
IDs; closure keeps Discord history and locks posting, with no implemented
automatic transcript export. Suggestions and streamer resources have their own
tables/lifecycles. Never add live content as source fixtures.

Owner /server setup inspects then confirms focused repair. /server health is
read-only. Targeted sync commands have narrower contracts: sync-support refreshes
adopted boards, while instant-gaming can create/recover its four channels. Do not
assume all commands named sync have identical side effects.

`cogs/server_changes.py` adds persistent staff approval buttons over
`services/channel_change_service.py`. Channel update/delete events compare monitored
support/IG IDs with existing desired state; exact expected edits suppress self-events.
Four-second coalescing, persisted pending records and existing owner/admin checks keep
adoption explicit. High-risk private/bot permission drift invokes the existing safe
permission helpers immediately. See [scope, actions and recovery](SERVER_STRUCTURE.md#detected-changes-and-approval).

Partner overview mentions come from persisted channel mappings; missing
destinations are omitted and reported. Gaming News and Gaming Deals lead MARKETPLACE, followed by Free Games, Amazon, AI Tools and Electricity,
while retaining the partner message key/affiliate button. Instant Gaming also
owns News and private Purchases/Buyer Ranking; missing external bot config must
not prevent channel preparation.

Fixed-message customization uses existing IDs plus managed_message_content and
managed_message_audit. Locks, version checks, canonical intent, pending delivery,
ownership/fingerprint checks and confirmed reset are detailed once in
[Managed messages](MANAGED_MESSAGES.md). Selectors/LFG cards retain their own
specialized renderers; do not force them into the public pin editor.

Legacy energy/household migration journals IDs and retires known defaults while
preserving custom/uncertain content and old tickets. legacy_finance_service can
delete a recorded, dependency-free, fully inspected legacy finanzberatung only
during explicit owner repair. Health shares inspection, not deletion. See
[partner migration](PARTNERS.md) for its full safety contract.

## Operations and current constraints

Compose separates /opt/gamerhq/app from private environment, data and backups;
the seed stays visible inside the image. Backup uses SQLite backup/verification,
and production preflight rehearses migrations on a temporary copy. Host locks
coordinate updates/backups. See [deployment](../DEPLOY.md) and
[rollback](../ROLLBACK.md); do not duplicate their operational commands here.

Identity resolution and permission logic are feature-specific; some legacy/global
setting keys coexist with guild-scoped keys. Cross-feature changes must inspect
both callers and tests. Do not silently rename keys, assume multi-process safety
or perform a cleanup/refactor simply to make this architecture diagram stricter.

Optional onboarding and role boards are owned by `cogs/roles.py`, `role_service.py` and `role_panel_service.py`; per-game LFG preferences reuse the Games selector. See [role settings and migration](ROLE_SETTINGS.md).

`bot_group_service` orchestrates verified-identity Music Bots/Gaming Bots grouping during explicit owner Repair, reusing `music_bot_service` mappings and access logic. `instant_gaming_service` retains private IG resource keys while migrating channel IDs into AFFILIATE STATS. `support_service` owns Free Games and its independent editor pin; only DealGecko receives its scoped posting grant. No external bot API or automatic kick is used.

`cogs/gocdkeys.py` owns the existing `/deals` group; `cogs/deal_editor.py` implements actor-bound modal/preview/edit/cancel/confirm controls. `services/curated_deal_service.py` validates supplied EUR prices/HTTPS partner URLs, renders cards and fixes delivery to the persisted gaming-deals ID. `database/affiliate_deals.py` adds curated delivery claims via the existing SQLite connection; `curated_deals` stores metadata JSON, actor/time, target and delivery result. Confirmation reserves a unique draft ID before sending; retries cannot duplicate that draft. Preview is memory-only. No product data is fetched. `cogs/deal_import.py` adds a bounded paste/preview/title-edit/confirm workflow to that same group. `services/gocdkeys_import_service.py` coordinates URL imports with the shared curated target/delivery helpers; URL/referral/title parsing stays in `gocdkeys_service.py`. Imports use the existing `curated_deals` table: a guild + normalized-URL hash is the atomic delivery key; source/validated/normalized URLs and workflow metadata stay in its JSON. No schema migration or second provider is introduced. Existing automatic source mappings remain intact; `services/gocdkeys_service.py` retains parser/referral/provider seams but automatic lookup/listeners/backfill are unsupported and disabled in code regardless of legacy opt-in. See [deals](DEALS.md) and [GoCDKeys](GOCDKEYS.md).

Bot identity defaults live only in `config.THIRD_PARTY_BOTS`. `bot_group_service` reuses persisted role IDs, performs bounded exact-ID member fetches during Repair, and exposes the fetched identity to existing feed/private permission helpers. Health reports each membership without network fetches. Short-lived successful assignment tracking covers Discord gateway cache lag; integration roles are preserved.

The existing GoCdKeysService is the shared paid-deal pipeline for trusted bot identities: price classification precedes validated lookup; scoped raw edits update the owned persisted companion. No second provider or channel registry is introduced.

Streamer Hub: the existing streamer cog registers `cogs/twitch_hub.py` buttons and starts/stops `services/twitch_service.py`. `services/streamer_hub_service.py` owns authorization, managed resources and read-only health; `database/twitch.py` uses additive SQLite connection/session tables. OAuth Device flow and EventSub are outbound-only; default flags are false. Legacy profile records remain untouched. See [beta contract](STREAMER_HUB.md).

## Profile settings

`cogs/roles.py` reuses RoleSelectionSession for initial personal onboarding and later
corrections: Gender → Age → Review → Save. `role_service.py` limits the save allowlist
to Gender/Age, rechecks mappings/member state and uses the same per-member lock as
quick toggles. Game/platform/notification and unrelated roles remain independent.
`role_panel_service.py` manages four existing slots: Profile Settings, Gaming Setup,
Interests & Notifications and Suggest Role. Explicit setup Repair retires the proven
old About You pin and marks legacy language mappings inactive without deleting Discord
roles/memberships. Health remains read-only. No automatic join questionnaire or
parallel profile subsystem is introduced; Welcome's Get Started is the existing entry.
See [profile contracts and migration](ROLE_SETTINGS.md).

Member-facing copy should be natural, friendly and concise. GamerHQ is English.
Hide empty/inactive sections completely; never publish configuration, provider or
mapping status as member-facing placeholders. Technical diagnosis belongs in staff
health/setup reports and logs. Member errors should offer a useful retry/help action.

Marketplace reuses the `partners-benefits` category key and the `household` canonical message key for identity continuity. Explicit Repair migrates old channel/adoption mappings to `electricity` before renaming; historical ticket types remain readable. New private requests use `ELECTRICITY_REQUEST`. See [migration details](PARTNERS.md).


## Internal maintenance audit (2026-09)

The repository-wide static review covered tracked Python modules, configuration,
CI/deployment scripts, dependencies and the documented feature boundaries. There
are no separate repositories/, models/ or views/ packages to reconcile: domain
persistence stays in database/, and Discord components stay in cogs/.

Focused changes preserve commands, component labels, copy, mappings, limits,
feature flags and provider links:

- `database.db.get_settings` batches parameterized reads. Channel order planning
  uses a fresh call-local snapshot, validates adopted IDs and reuses the existing
  desired-state rules. It introduces no process cache or schema migration; code
  performing async mutations continues to recheck live authorization/state.
- Role-admin views and legacy Game Library confirmations reuse the current-member
  owner/admin policy in `authorization_service` and the private response helper.
  Rights are checked at confirmation and after its initial Discord acknowledgement.
  Removed/revoked administrators cannot reuse an old panel. Managed-message
  authorization retains its existing import-compatible entry point.
- `services.url_service.validate_url` owns the existing public HTTPS policy;
  managed_message_service retains its import-compatible entry point. Validation
  never fetches or rewrites URLs. Provider-specific path/host rules remain separate.
- Bot shutdown attempts heartbeat, Twitch and Discord cleanup even if an earlier
  cleanup raises. Errors remain observable. Startup ordering is unchanged.
- Command guides reuse the existing channel alias function; unused private-role
  overwrite construction was removed. Staff guide pagination already exists and
  is retained. aiohttp is explicitly declared at the already locked version.

The synthetic default seven-board order-plan regression measures **2 SQLite
connections instead of 15**. This is a request-count improvement, not a claim
about live latency. The tests cover adopted positions/categories, Free Games
adjacency, ID conflicts, unrelated children, repeated reads, transaction rollback,
URL policy, admin revocation and shutdown failures.

Deferred deliberately: decomposing the large health scan/game cog, consolidating
feature-specific resolvers with different ownership rules, converting synchronous
SQLite access to async, enabling WAL, pooling Twitch HTTP sessions, and replacing
legacy modal components. These need their own characterization because identity,
transactions, cancellation or UI representation could change. Historical ticket
readers, migration journals and disabled-provider compatibility code remain live
compatibility dependencies, not dead code. Existing locks assume one bot process.
No dependency upgrade, new feature, Discord migration or deployment was introduced.
