# 🎮 GamerHQ

GamerHQ is a Discord-based gaming community hub for finding players, organizing gaming sessions, managing temporary voice channels and experimenting with community features.

> GamerHQ is an actively developed personal/community project. Features and server structure may continue to evolve.

## Features

### Gaming & LFG

- Start with optional gender/age groups, select games, and manage separate notification/profile preferences. See [role settings](docs/ROLE_SETTINGS.md).
- Create public or private Looking for Group sessions, invite players and join private sessions with access codes.
- Manage active lobbies: hosts can edit details, manage participants, review proposed times and close or cancel sessions.
- Maintain a game library with optional dedicated Game Areas. Game selection and a game's Discord area are separate: removing an area can preserve the game and its role.

### Voice

- Join a Create Voice channel to create an owned temporary room.
- Rename your room, change its user limit, lock/unlock access, allow or remove players, and confirm room closure through `/voice manage`.
- Automatically clean up empty managed temporary rooms.
- Configure access for external music bots through a dedicated Music Bots role. GamerHQ provides integration permissions and usage guidance, not music playback itself.

### Community

- Submit suggestions through a public entry point for private Staff review.
- Open private support tickets with a short form. Staff can take tickets and mark them waiting; the creator or Staff can confirm closure. Closed tickets retain readable history.
- Use managed welcome, rules, guide and role-selection channels.
- Use the Partners & Benefits overview to open the read-only partner channels through clickable list entries. Need Support remains the separate private help-ticket entry.
- Provide tournaments/giveaways channels as an events foundation; event functionality remains Coming Soon.

Partner messages are English; Haushaltscheck remains German for its free, nonbinding Germany-focused household/contract requests. Gaming Deals highlights current deals, promotions and releases.

### Administration

- Optional [Instant Gaming setup](docs/INSTANT_GAMING.md) is an external owner configuration.

- Preview and confirm incremental server setup/repair, preserving managed channel identities where possible.
- Inspect read-only server health diagnostics and the deployed command inventory.
- Add or remove multiple Game Areas, with dependency checks and explicit removal confirmation.
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

🤝 PARTNERS & BENEFITS
├─ 📰・gaming-news
├─ 🔥・gaming-deals
├─ 🎁・free-games
├─ 🛒・amazon
├─ 🤖・ai-tools
└─ 🇩🇪・haushaltscheck

COMMUNITY
├─ newbies
├─ general
├─ introductions
├─ suggestions
└─ bot-commands

EVENTS
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

STAFF (private)
├─ staff-chat
├─ staff-suggestions
├─ ticket-logs
├─ mod-log
├─ bot-log
└─ mod-commands
```

Optional Game Areas, streamer areas, LFG resources and temporary voice rooms extend this layout. Existing server resources may differ; the diagram is a reference, not a promise that setup creates every item from an empty server.

**Need Support** opens a help ticket visible to its creator and authorized Staff. **Partners & Benefits** in support-gamerhq links directly to the six channels in **PARTNERS & BENEFITS**, where offers and their actions live. Ordinary members cannot post in either public entry channel. Private ticket channels allow conversation until closure.

Gaming News covers news/releases; Gaming Deals covers discounts, promotions and Instant Gaming offers; Free Games covers free/free-to-keep offers from DealGecko. Free Games is not an affiliate promotion board.

The intended bot stack is GamerHQ, **🤖 Gaming Bots** (Instant Gaming, DealGecko), and **🎵 Music Bots** (Jockie Music, Pancake). Group roles are hoisted below Staff and grant no blanket private access. Known DealGecko/Jockie/Pancake IDs are built in; the existing Instant Gaming identity remains configurable. Owner Repair handles grouping and scoped permissions. See [Discord setup](docs/DISCORD_SETUP.md).

## Requirements

- Python 3.12 or newer; the Docker image uses 3.12 and local installation checks have also run on 3.14.
- Git and the pinned Python packages in [requirements.txt](requirements.txt).
- SQLite, included with Python; no separate database server is required.
- A Discord bot application and a server you administer, only when running the bot against Discord.

There is no Node/web component. Offline tests need no Discord credentials or live server.

## Local installation

Clone the repository, then run these commands from its root. Replace the repository URL placeholder with your own URL.

### Windows PowerShell

```powershell
git clone "<YOUR_REPOSITORY_URL>" gamerhq
cd gamerhq
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m tools.test
```

### Linux / macOS

```bash
git clone "<YOUR_REPOSITORY_URL>" gamerhq
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
| `/lfg create`, `/lfg manage`, `/lfg join-code` | Create/manage sessions and join private sessions |
| `/voice manage` | Manage your own temporary room; Staff may select a managed room |
| `/streamer setup`, `/streamer profile` | Legacy staff entry points to the enabled Twitch beta; profiles/following are inactive |
| `/streamer area`, `/streamer channels`, `/streamer voice` | Retained legacy staff-only area tools |

### Administration commands

| Commands | Purpose |
| --- | --- |
| `/server health` | Owner/admin read-only diagnostics |
| `/server setup` | Owner-only inspection, preview and confirmed repairs |
| `/server sync-support` | Reconcile existing support/partner layout and pins without creating missing channels |
| `/server adopt` | Owner/admin preview and confirmation: persist selected current public board layout as desired state |
| `/server cleanup-game-areas` | Preview unused managed areas before confirmed cleanup |
| `/server music-bots-role` | Configure the existing dedicated Music Bots role |
| `/server roles` | Manage GamerHQ roles |
| `/server pinned-messages` | Owner/admin: edit managed pinned Markdown messages and buttons with preview/confirmation |
| `/area manage` | Add or safely remove multiple Game Areas |
| `/game-admin create`, `/game-admin rename`, `/game-admin delete` | Administer game-library entries |
| `/game-admin add-area`, `/game-admin remove-area`, `/game-admin setup` | Manage optional game areas |
| `/game-admin recover-existing`, `/game-admin set-visible` | Recover existing mappings or change selection visibility |
| `/game-admin overview`, `/game-admin status`, `/game-admin database` | Refresh the selector or inspect game/database state |

Staff review suggestions and take/mark waiting/close tickets through private buttons. Ticket creation and role-selection hubs also use components; they are not additional slash commands. Lobby controls remain subject to host/member authorization.

## Server setup and repair

1. Prepare the existing base layout described in [setup](docs/SETUP.md). START HERE and COMMUNITY must already exist. A private STAFF category is needed for Staff review/log placement; configure the existing game-selector channel as well.
2. Start the bot and run `/server health` to inspect missing resources, mappings and permissions without repairing them.
3. As server owner, open `/server setup`, review its check/repair preview and confirm the intended changes.
4. Confirm that each Partners & Benefits list item opens its partner channel. Missing mappings are omitted from navigation and reported by health; owner setup repairs them.
5. Run health again and perform the relevant [manual acceptance checks](RELEASE_CHECKLIST.md), including two-user ticket isolation and voice ownership.

Setup is an incremental maintenance workflow, not a complete empty-server installer. It reuses managed channels/messages, updates guides and permissions, and leaves ambiguous resources for manual review. Destructive Game Area cleanup has its own selection and confirmation; it is not an automatic consequence of opening health/setup.

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

- **Events:** tournaments/giveaways have channels and Coming Soon guidance; a full event platform is not implemented.
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

support-gamerhq stays in START HERE and explains direct support and support through useful partner/deal links. PARTNERS & BENEFITS has this managed order: Gaming News, Gaming Deals, Amazon, AI Tools, Haushaltscheck. Owner Repair safely removes the recorded direct-support channel; unknown content or dependencies remain for review. Instant Gaming purchases and buyer ranking remain staff-only. Owner `/server setup` safely migrates recorded legacy partner boards to Haushaltscheck and deletes confidently managed, dependency-free finanzberatung only after full content/thread checks; uncertain cases report an exact MANUAL_REVIEW reason; `/server sync-support` refreshes adopted boards. See [final message texts, migration and tests](docs/PARTNERS.md).

Owner/admin `/deals import-gocdkeys` imports up to ten copied partner links through a private preview, editable titles and confirmed individual posts; repeated imports skip stored URLs. [Manual import workflow](docs/GOCDKEYS.md#manual-batch-import).

Owner/admin `/deals create` previews curated Amazon, Instant Gaming, GoCDKeys or other partner deals before posting to the stored gaming-deals channel. Prices and verified links are supplied manually; the existing Amazon link and official Instant Gaming posts remain unchanged. [Deal workflow](docs/DEALS.md). GoCDKeys automatic lookup/backfill is currently unsupported (HTTP 403); [manual links remain available](docs/GOCDKEYS.md). Free Games remains separate.
