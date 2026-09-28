# Security boundaries for maintainers

This describes the current code and offline test contracts, not a live-server
acceptance result. See [Security](../SECURITY.md), [commands](COMMANDS.md) and
[release acceptance](../RELEASE_CHECKLIST.md). No security task enables the Twitch
beta or runs a production bot.

## Command inventory and authorization

The 36 registered slash commands use the following minimum boundaries. Discord
command visibility is only a convenience; decorators, cog checks and owning
services enforce access. Server ownership includes Administrator permissions;
`/server setup` deliberately retains its stricter owner-only policy.

| Classification | Commands / component actions | Boundary |
| --- | --- | --- |
| MEMBER | `/game select`, `/game suggest`, `/lfg create`, `/lfg join-code` | Current guild membership; roles are opt-in managed mappings, private invitations and game access are checked separately. |
| HOST/OWNER-OF-RESOURCE | `/lfg manage`, `/voice manage` | Persisted host/owner and guild; Staff may manage scoped voice rooms. Lobby participants cannot invoke host actions. |
| ADMIN | `/game-admin database`, `recover-existing`, `status`, `set-visible`, `create`, `add-area`, `remove-area`, `delete`, `rename`, `setup`, `overview` | Administrator command checks; destructive previews retain current authorization and dependency checks. |
| ADMIN | `/server health`, `adopt`, `instant-gaming`, `music-bots-role`, `cleanup-game-areas`, `roles`, `sync-support`, `pinned-messages`; `/area manage` | Owner/Administrator according to existing policy. Health is read-only and ephemeral. Normal Moderator permissions do not qualify. |
| ADMIN | `/deals create`, `/deals import-gocdkeys`, `/deals backfill` | Current owner/admin checked in command, preview and publishing services; automatic backfill/provider lookup is disabled. |
| SERVER_OWNER | `/server setup` | Owner-bound confirmation and guild check; serialized full repair. Unknown/manual resources retain review/deletion safeguards. |
| MODERATOR | `/streamer area`, `/streamer channels`, `/streamer voice` | Legacy Staff-only tools, additionally scoped to the caller's stored profile/resources. Old channel modals recheck current Staff access. |
| MEMBER, conditional beta | `/streamer setup`, `/streamer profile`, `/streamer audience` | Enabled beta and approved Streamer/Staff membership; audience remains inactive. Both feature flags default false. |
| MEMBER / RESOURCE OWNER | Ticket entry, own ticket close, profile/game selectors, LFG join/leave and proposals | Stored guild/resource identity, explicit member choices and current state. No arbitrary role IDs or general channel-management grants. |
| MODERATOR | Suggestion review and ticket take/wait/close buttons | Current Staff policy: owner, or roles with Administrator, Manage Guild, Manage Messages or Moderate Members. Private visibility alone is insufficient. |
| ADMIN | Managed pin editor and managed-change approval buttons | Actor/session/guild, current owner/admin, resource identity and stored revision; allowlisted message actions. |
| PUBLIC / DEV_ONLY | None | No registered anonymous, debug, simulation, development or general moderation slash commands. Suggestions/tickets are button flows, not slash groups. |

## Roles, privacy and abuse controls

Game selection checks the current selectable mapping against the reviewed role ID,
rejects role aliases shared with other managed roles/games, rejects permissions or
integration-managed roles, and checks Manage Roles plus the bot hierarchy. It
refreshes membership inside the existing per-member lock and applies explicit
add/remove choices. Game, profile and notification choices share that lock;
notification roles remain separate from game access. One confirmation is consumed
before any asynchronous write. Discord can still partially apply a multi-role
request; failure asks the member to inspect current roles before retrying.

Suggestions reserve SQLite delivery state transactionally before posting. Exact
retries reuse the recent reservation; different submissions from the same member
are limited to one per 30 seconds. Lobby creation similarly stores a per-member,
per-guild 30-second cooldown in existing settings and rejects an identical active
lobby. A draft can submit once. Uncertain private-channel creation retains the
lobby record for Staff inspection instead of silently allowing another creation. Voice creation serializes gateway requests per
member and guards rapid repeats for five seconds; missing/moved game categories
are never replaced with public root-level rooms.

Tickets retain existing per-type limits, reservations, current creator/Staff
checks and private overrides. Closed tickets remain private and readable. There
is no member-controlled participant addition, Staff Notes or transcript-export
feature to secure. Temporary voice controls stay scoped to persisted rooms;
remove means disconnect, not kick. Staff, tickets and Affiliate Stats do not grant
ordinary members visibility. Private LFG access, exclusions, capacity and host
actions are checked against transactional state, including current invite tokens.

Deal posting/import retains durable delivery claims, admin-only previews and
provider-specific HTTPS domain/path checks. Public URLs reject credential-bearing
or secret query links, unsafe schemes and control characters. Referral parameters
remain public content. Generic link validation never fetches a URL. Untrusted
LFG mentions are escaped; managed pins, suggestions and promotions use restricted
allowed mentions. Pin editing cannot target arbitrary user messages or callback
IDs. Existing Staff-guide pagination remains in place.

## Recovery and operational limits

Persistent views register on startup for managed entries, tickets, suggestions,
lobbies, invitations and proposals. Temporary editors expire and must be reopened. LFG and managed-change maintenance
log transient SQLite failures privately and retry on the next scheduled cycle.
Mapped message IDs, revision checks and delivery claims prevent blind reposting;
uncertain deliveries require inspection, not automatic retries. Existing private
state and unknown resources must not be recreated from names alone.

Twitch uses outbound Device OAuth, not a callback server. The temporary device
attempt, Discord guild/user and confirmation nonce bind the connection; validated
Twitch account/client IDs and uniqueness constraints bind persisted tokens. Beta
membership is checked again for live posting, and stream session claims suppress
duplicates. HTTP has bounded timeouts and redirects are disabled; reconnect hosts
are restricted. Tokens are not included in provider error text. Production flags
remain disabled, and tokens in SQLite/backups require private host permissions.

Run **one bot process per guild/database**. SQLite uses parameterized values,
whitelisted dynamic update columns, closing connection contexts and transactional
claims. Its default five-second busy timeout can block the event loop under
contention; no WAL/async database migration is included. Backup uses SQLite's
backup API, exclusive private destination creation and integrity verification.
Pruning only targets verified dated backups; update/backup scripts coordinate with
a host lock. Discord inputs do not select filesystem paths.

Offline coverage includes `test_security_hardening.py` plus existing role/profile,
managed-message, lobby, ticket, privacy, Twitch, production and repository-safety
tests. Run `python -m pytest` and `python -m tools.repository_audit --history` from
the project root. Dependency checks and CI's shell/Compose/image checks supplement
the suite. This does not prove live Discord permission inheritance, external bot
configuration, provider availability, SELinux mounts or host recovery; use the
release checklist before a VPS launch. Secret scanning is heuristic and covers
available local Git refs, not remote or unreachable historical objects.
