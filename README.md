# 🎮 GamerHQ

GamerHQ is a Discord-based gaming community hub for finding players, organizing gaming sessions, managing temporary voice channels and experimenting with community features.

> GamerHQ is an actively developed personal/community project. Features and server structure may continue to evolve.

## Engineering direction

GamerHQ is being evolved toward a modular, portable **Skill Runtime / SDK** with GamerHQ as its first host. The long-term direction includes third-party Skill development and an AI-ready capability layer while keeping today's Discord server stable and maintainable. See the [Engineering Vision](docs/VISION.md), [Skill Runtime documentation](docs/skills/README.md), and the [Skill Developer Guide](docs/skills/developer-guide.md).

## Features

### Gaming & LFG

- **Choose Your Games** manages games. **Profile Settings / Choose Your Roles** offers Gender/Age corrections plus quick Gaming Setup and Interests & Notifications. Initial onboarding and Update Profile use Gender → Age → Review → Save. GamerHQ is English; language selection is not offered. See [profile settings](docs/ROLE_SETTINGS.md).
- Create public or private Looking for Group sessions, invite players and join private sessions with access codes.
- Manage active lobbies: hosts can edit details, manage participants, review proposed times and close or cancel sessions.
- Choose games in a personal panel with dynamic Popular Top 25 and complete A–Z browsing. Each choice immediately adds/removes your game role; it never enables notification pings.
- Games may have one optional role-gated text channel under **🎮 GAMING**. Roles work without channels; LFG and temporary voice remain centralized. See [the game system](docs/GAME_SYSTEM.md).

### Voice

- Join a Create Voice channel to create an owned temporary room.
- Rename your room, change its user limit, lock/unlock access, allow or remove players, and confirm room closure through `/voice manage`.
- Automatically clean up empty managed temporary rooms.
- Configure access for external music bots through a dedicated Music Bots role. GamerHQ provides integration permissions and usage guidance, not music playback itself.

### Community

- Submit suggestions through a public entry point for private Staff review.
- Open private support tickets with a short form. Staff can take tickets and mark them waiting; the creator or Staff can confirm closure. Closed tickets retain readable history.
- Use managed welcome, rules, guide and role-selection channels.
- Use the Marketplace overview to open the read-only partner channels through clickable list entries. Need Support remains the separate private help-ticket entry.
- Provide community-events, tournaments and giveaways boards; community activities have their own space.

Marketplace messages are English. Electricity is a Germany-only electricity tariff comparison/request service. Gaming Deals highlights current deals, promotions and releases.

### Administration

- `/server setup` guides first-time configuration; `/server manage` opens everyday administration; owner-only `/server dev` keeps technical tools available. Changes retain existing previews and confirmations.
- Private STAFF `server-log` announces deployed versions once and directs owners to settings needing review. See [production operations and safe DB recovery](docs/PRODUCTION_OPERATIONS.md).
- Optional [Instant Gaming setup](docs/INSTANT_GAMING.md) is an external owner configuration.

- Inspect read-only diagnostics through Server Dev when deeper investigation is needed.
- Use Server Management → Games for the library, channels and candidates. At 10 role members, a private games-log candidate invites admin approval; the initial channel soft limit is 20 with an explicit override.
- Manage game-library entries, managed roles, information messages and Music Bots role configuration.
- Edit GamerHQ-managed pinned messages with `/server pinned-messages` (owner/admin): Markdown, configurable link/action buttons, Preview → Save, and independent selection of multiple messages in a channel. Customizations survive restart and repair; confirmed Reset to Default restores generated content. See the [editor guide](docs/MANAGED_MESSAGES.md).

### Streamer Hub — hidden beta

- Approved streamers can connect Twitch; EventSub posts title/category and a Watch on Twitch button in stream-updates when enabled.
- Both beta and role self-selection default to **false**. choose-streamers, public profiles, following and custom live messages are inactive.
- See [Twitch beta setup and limitations](docs/STREAMER_HUB.md).

## Discord server structure

An example of the current managed layout, with decorative channel prefixes omitted for readability:

```text
START HERE
├─ welcome
├─ rules
├─ announcements
├─ choose-your-games
├─ choose-your-roles
├─ looking-for-group
├─ guide
├─ need-support
└─ support-gamerhq

🛒 MARKETPLACE
├─ 📰・gaming-news
├─ 🔥・gaming-deals
├─ 🎁・free-games
├─ 🛒・amazon
├─ 🤖・ai-tools
└─ 🇩🇪・electricity

COMMUNITY
├─ newbies
├─ general
├─ introductions
├─ suggestions
└─ bot-commands

EVENTS
├─ community-events
├─ tournaments
└─ giveaways

STREAMERS (hidden beta)
├─ stream-updates
├─ streamer-guide
└─ streamer-commands

VOICE CHANNELS
├─ Chill Lounge
├─ Create Voice
└─ AFK

SUPPORT TICKETS (private)
└─ ticket-0042 (example; created on demand)

🔒 AFFILIATE STATS (private)
├─ 💸・purchases
└─ 🏆・buyer-ranking

🎮 GAMING (each optional channel requires its game role)
├─ counter-strike-2
├─ minecraft
└─ valorant

STAFF (private)
├─ staff-chat
├─ staff-suggestions
├─ ticket-logs
├─ mod-log
├─ server-log
├─ 🎮・games-log
├─ bot-log
└─ mod-commands
```

Optional single game channels, streamer areas, LFG resources and temporary voice rooms extend this layout. Legacy multi-channel Game Areas are deprecated and retained only for reviewed migration. Existing server resources may differ; the diagram is a reference, not a promise that setup creates every item from an empty server.

**Need Support** opens a help ticket visible to its creator and authorized Staff. **Marketplace** in support-gamerhq links directly to the six channels in **MARKETPLACE**, where offers and their actions live. Ordinary members cannot post in either public entry channel. Private ticket channels allow conversation until closure.

Gaming News covers news/releases; Gaming Deals covers discounts, promotions and Instant Gaming offers; Free Games covers free/free-to-keep offers from DealGecko. Free Games is not an affiliate promotion board.

The intended bot stack is GamerHQ, **🤖 Gaming Bots** (Instant Gaming, DealGecko), and **🎵 Music Bots** (Jockie Music, Pancake). Group roles are hoisted below Staff and grant no blanket private access. Known DealGecko/Jockie/Pancake IDs are built in; the existing Instant Gaming identity remains configurable. `/server repair` handles linked bot grouping and scoped permissions. See [Discord setup](docs/DISCORD_SETUP.md).

## Requirements

- Python 3.12 or newer; the Docker image uses 3.12 and local installation checks have also run on 3.14.
- Git and the pinned Python packages in [requirements.txt](requirements.txt).
- SQLite, included with Python; no separate database server is required.
- A Discord bot application and a server you administer, only when running the bot against Discord.

There is no Node-based web application in this repository. The optional internal HTTP transport for the separate trusted web BFF is documented in [docs/web-api.md](docs/web-api.md). Offline tests need no Discord credentials or live server.

## Local installation

Clone the GamerHQ repository, then run the remaining commands from the project root.

### Windows PowerShell

```powershell
git clone "https://github.com/Duy-Phan96/GamerHQ.git" gamerhq
cd gamerhq
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m tools.test
```

### Linux / macOS

```bash
git clone "https://github.com/Duy-Phan96/GamerHQ.git" gamerhq
cd gamerhq
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
.venv/bin/python -m tools.test
```

Use a Python installation meeting the version requirement. Copy `.env.example` only for a new installation; do not overwrite an existing private `.env` during updates. See [detailed setup](docs/SETUP.md) for database initialization and maintenance utilities.

## Configuration

Edit your private `.env` before starting the bot. Existing process environment variables take precedence over `.env`.

| Variable | Purpose |
| --- | --- |
| `DISCORD_TOKEN` | Required to start; your private bot token |
| `GUILD_ID` | Required positive Discord server ID; target for guild command registration |
| `DEALGECKO_BOT_ID`, `JOCKIE_MUSIC_BOT_ID`, `PANCAKE_BOT_ID` | Optional positive overrides for the built-in public bot IDs; unset/`0` uses the known identity |
| `INSTANT_GAMING_BOT_ID` | Optional verified external bot user ID; repair grants posting in gaming-news/gaming-deals and private Affiliate Stats purchases/buyer-ranking |
| `GAMERHQ_DB_PATH` | Private SQLite path; the example uses `runtime/data/gamerhq.db` |
| `CHOOSE_GAMES_CHANNEL_ID` | Existing game-selector channel; configure before using its overview |
| `GAME_SUGGESTIONS_CHANNEL_ID` | Optional legacy setting; current suggestions use managed private Staff mappings |
| `INTRODUCTIONS_CHANNEL_ID` | Optional legacy compatibility setting |

Relative database paths resolve from the repository directory. Omitting `GAMERHQ_DB_PATH` preserves the legacy `gamerhq.db` default. The schema and public game catalog are initialized from source on bot startup; no production database download is needed.

The actual configuration template is [.env.example](.env.example). The disabled Twitch beta needs no credentials; future Dev testing uses TWITCH_CLIENT_ID for Public-client Device OAuth. No client secret, callback listener or PostgreSQL connection is used.

## Running the bot

Create/configure your Discord application, enable its **Server Members Intent**, install it with bot/application-command access and configure the permissions and role hierarchy described in [Discord setup](docs/DISCORD_SETUP.md). The bot's code also enables voice-state events; it does not enable privileged Presence or Message Content intents.

After configuration:

```powershell
.\.venv\Scripts\python.exe bot.py
```

On Linux/macOS:

```bash
.venv/bin/python bot.py
```

Starting the bot connects to Discord, initializes SQLite, synchronizes commands for `GUILD_ID`, clears this application's global commands and reconciles known resources. Use a separate application, server and database for development. Run only one instance against a guild/database.

## Commands

These commands are defined in the extensions loaded by `bot.py`. The [full command reference](docs/COMMANDS.md) describes all 31 registered slash commands; `/server health` Details shows the deployed inventory.

### Member commands

| Commands | Purpose |
| --- | --- |
| `/game select`, `/game suggest` | Choose game roles or suggest a game |
| `/lfg create`, `/lfg manage`, `/lfg join-code` | Create/manage scheduled events and join private events |
| `/voice manage` | Manage your own temporary room; Staff may select a managed room |
| `/streamer setup`, `/streamer profile` | Legacy staff entry points to the enabled Twitch beta; profiles/following are inactive |
| `/streamer area`, `/streamer channels`, `/streamer voice` | Retained legacy staff-only area tools |

### Administration commands

| Commands | Purpose |
| --- | --- |
| `/server health` | Fast owner/admin diagnostics; Details runs deeper message checks |
| `/server setup` | Owner-only creation of genuinely missing resources after preview/confirmation |
| `/server repair` | Owner/admin confirmed fixes to linked resources only |
| `/server reconcile` | Owner/admin confirmed linking of existing Discord IDs |
| `/server message-duplicates` | Review duplicates separately from structure warnings; confirm keep/remove |
| `/server sync-support` | Reconcile existing support/partner layout and pins without creating missing channels |
| `/server adopt` | Owner/admin preview and confirmation: persist selected current public board layout as desired state |
| `/server cleanup-game-areas` | Preview unused managed areas before confirmed cleanup |
| `/server music-bots-role` | Configure the existing dedicated Music Bots role |
| `/server roles` | Manage GamerHQ roles |
| `/server pinned-messages` | Owner/admin: edit managed pinned Markdown messages and buttons with preview/confirmation |
| `/area manage` | Legacy area inspection/removal; new area creation is disabled |
| `/game-admin create`, `/game-admin rename`, `/game-admin delete` | Administer game-library entries |
| `/server manage` → Games | Library, optional single channels and threshold candidates |
| `/server dev` → Legacy Game Migration | Preview moving an existing chat, preserving its ID/history |
| `/game-admin recover-existing`, `/game-admin set-visible` | Recover existing mappings or change selection visibility |
| `/game-admin overview`, `/game-admin status`, `/game-admin database` | Refresh the selector or inspect game/database state |

Staff review suggestions and take/mark waiting/close tickets through private buttons. Ticket creation and role-selection hubs also use components; they are not additional slash commands. Event management remains creator-only; participants can join, leave and use shared event links.

## Server operations

Use `/server health` for read-only diagnosis, `/server reconcile` to link existing Discord IDs, `/server repair` for confirmed fixes to linked resources, and owner `/server setup` only for genuinely missing resources. `/server message-duplicates` reviews duplicate messages independently of unrelated structure warnings. All mutation flows require explicit confirmation; unknown/private resources and custom content stay protected.

See the canonical [production operations guide](docs/PRODUCTION_OPERATIONS.md) for VPS updates, backups, safe diagnostics and fresh-DB recovery. Local installation remains in [setup](docs/SETUP.md). Validate ordinary-member/private access using the [release checklist](RELEASE_CHECKLIST.md).

## Testing

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m tools.test
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m tools.repository_audit --history
```

On Linux/macOS, use `.venv/bin/python` with the same module commands. The test runner disables local `.env` loading, uses temporary SQLite and blocks Discord HTTP requests. Coverage includes business rules, persistence, permissions and mocked Discord interactions. The repository audit reports potential secrets by location/type without printing their values.

The GitHub Actions workflow runs offline tests on Python 3.12/3.14, repository and shell checks, Compose validation and a production image build; it does not deploy. No separate live integration/web suite, linter, formatter or type checker is configured. See [contributing](CONTRIBUTING.md) for the development workflow.

## Planned work and current limits

- **Events:** community-events hosts game nights and community activities, alongside separate tournaments/giveaways boards; a full event platform is not implemented.
- **Streamer Hub:** hidden beta implemented but disabled by default; live Twitch acceptance must be performed separately on a Dev server.
- **Potential experiments:** participant availability feedback, post-session feedback and simpler game-role selection remain ideas under evaluation, not shipped features or delivery commitments.
- **Tickets:** closed history stays in private Discord channels. Automatic transcript export, retention/deletion, reopening, Staff Notes and Add User are not implemented.
- **XP, levels and achievements:** not implemented; no reward system is promised.

## Security and privacy

Never commit `.env`, Discord tokens, private keys, runtime SQLite files, logs, backups, uploads or ticket/transcript exports. The game seed contains public catalog data; tests use synthetic community data. Rotate leaked credentials immediately—adding an ignore rule does not remove secrets from earlier Git history.

Do not include real ticket text, private invite codes or unredacted user data in bug reports. Report security issues privately to the repository owner as described in [SECURITY.md](SECURITY.md). Back up runtime data separately from source code and preserve ticket history before any future manual deletion.

## Further documentation

- [Repository instructions](AGENTS.md) and [development workflow](docs/DEVELOPMENT_WORKFLOW.md): selective context, validation and reusable task prompts.

- [Architecture](docs/ARCHITECTURE.md): interaction layer, services, persistence and managed Discord resources.
- Production uses Docker Compose on AlmaLinux, external private configuration/data, a non-root container, daily verified backups (14 daily snapshots), log rotation and owner-run updates from `main`.
- [Deployment](DEPLOY.md) and [rollback](ROLLBACK.md): owner-controlled operations with separate runtime storage.
- [Release checklist](RELEASE_CHECKLIST.md): manual Discord acceptance before a release.
- [Changelog](CHANGELOG.md): development history.

## License

LICENSE is not configured. The repository is public, but no software license has been selected; that remains an owner decision.

support-gamerhq stays in START HERE and links to MARKETPLACE: Gaming News, Gaming Deals, Free Games, Amazon, AI Tools, Electricity. Purchases and buyer ranking stay private. Existing mappings and customized messages are preserved; legacy channel retirement requires separate owner review. See [partner details](docs/PARTNERS.md) and [production operations](docs/PRODUCTION_OPERATIONS.md).

Owner/admin `/deals import-gocdkeys` imports up to ten copied deal/giveaway partner links through a private preview, editable titles and confirmed individual posts; repeated imports skip stored URLs. [Manual import workflow](docs/GOCDKEYS.md#manual-batch-import).

Owner/admin `/deals create` previews curated Amazon, Instant Gaming, GoCDKeys or other partner deals before posting to the stored gaming-deals channel. Select `promotion_type:GIVEAWAY` for the existing giveaways channel with optional prize/end date/note and durable URL deduplication. Prices and verified links are supplied manually; the existing Amazon link and official Instant Gaming posts remain unchanged. [Deal workflow](docs/DEALS.md). GoCDKeys automatic lookup/backfill is currently unsupported (HTTP 403); [manual links remain available](docs/GOCDKEYS.md). Free Games remains separate.

Local and VPS SQLite databases are separate runtime stores. Before cutover, migrate the existing runtime snapshot or use safe reconciliation; never run local and production GamerHQ simultaneously against the live guild. See [database migration and duplicate review](docs/DATABASE_MIGRATION.md).
