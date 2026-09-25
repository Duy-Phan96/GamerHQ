# Local installation

## Windows PowerShell

From the repository root, using Python 3.12 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m tools.test
.\.venv\Scripts\python.exe -m pip check
```

## Linux/macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
.venv/bin/python -m tools.test
.venv/bin/python -m pip check
```

Never overwrite an existing private `.env` when updating. Offline tests do not load it. Set the real variables in `.env` only when you intend to connect your development bot. Use separate development credentials, guild and SQLite path. `GAMERHQ_DB_PATH=runtime/data/gamerhq.db` is a portable local choice; relative paths resolve from this repository, independent of the terminal directory. Existing installs without that setting retain their legacy `gamerhq.db` path.

## Database construction

Schema and additive migrations live in `database/db.py`; the public game-only seed is `data/games_seed.json`. They are initialized automatically on real bot startup. No database download or production snapshot is needed. Tests demonstrate initialization into a new temporary nested directory and verify private tables are empty.

For an explicitly configured development DB only, initialization without Discord is:

```powershell
.\.venv\Scripts\python.exe -c "from database import db; db.init_db(); db.seed_catalog()"
```

This command writes to your configured DB; check `.env` first. It is not part of the credential-free test runner.

## Starting deliberately

After [Discord setup](DISCORD_SETUP.md):

```powershell
.\.venv\Scripts\python.exe bot.py
```

On Linux/macOS use `.venv/bin/python bot.py`. Missing token/guild configuration fails before login; malformed numeric settings name the variable without displaying its contents.

Startup synchronizes guild commands and removes global commands belonging to that application. It also restores known messages/resources. Do not use production credentials for development. Run `/server health` first; owner `/server setup` previews and confirms incremental changes. This repository has no offline simulator for a complete live guild and no deployment step is part of unit tests.

## Existing-server assumptions

Before core repair, create START HERE and COMMUNITY categories. Add the channels the health report expects: rules, announcements, choose-your-games, choose-your-roles, looking-for-group, general, introductions, suggestions, tournaments and giveaways. Configure `CHOOSE_GAMES_CHANNEL_ID` to the existing selector. An existing private STAFF category is required for staff review/log placement. Voice and streamer resources follow their existing feature/setup flows; inspect the health report rather than expecting setup to rebuild all missing resources. Review generated roles and bot hierarchy before member testing.

## Maintenance utilities

`python -m tools.backup_database <PRIVATE_BACKUP_PATH>` creates a verified SQLite backup from the configured DB. `python -m tools.production_preflight --db-path <EXISTING_PRIVATE_DB>` checks an already configured deployment; it rehearses migrations on a temporary copy. `--allow-new` explicitly permits a missing DB but does not create it. See [production setup](../DEPLOY.md) for external paths, directory permissions and backups. `tools/cleanup_catalog.py` is retained legacy maintenance source, not a setup step; it changes catalog/database data and must not be run casually. Local ignored root patch scripts and old README variants are not installation dependencies.

## Partner navigation verification

After owner setup, START HERE / support-gamerhq should have six clickable list entries pointing to the read-only MARKETPLACE channels: gaming-news, gaming-deals, free-games, amazon, ai-tools and electricity. There is no separate mention footer. `/server sync-support` refreshes adopted messages; missing/deleted channel mappings are omitted from navigation and reported by `/server health`. Use owner `/server setup` → Repair to restore the structure/mappings, then verify the list again. Existing canonical message IDs and unrelated pins are preserved.

## Managed message editor upgrade

Restart the updated bot to initialize the additive managed-content/audit tables, then run `/server health` and owner `/server setup` → Repair to register the supported public boards. No new environment variables are required. Owner/admin `/server pinned-messages` selects a channel, then a message when there are multiple pins. Test Electricity and Amazon independently: edit Markdown/buttons, Preview, Save Changes, and verify the selected message ID and pin are unchanged. Run Repair again to verify that customization persists. See [editor usage and recovery](MANAGED_MESSAGES.md).

Default boards keep receiving generated navigation updates. Customized boards retain their complete saved body and button configuration, including any manually written mentions; review those links after changing server structure or explicitly Reset to Default. Keep the private SQLite database through deployments.

For optional deal posting, follow [Instant Gaming setup](INSTANT_GAMING.md). Set the verified external bot ID in private configuration; restart and run owner Repair to grant its narrow posting access. No partner-account credentials belong in GamerHQ.

Verify partner language after repair: overview, Gaming News, Amazon, Gaming Deals and AI Tools are English; Electricity is English and available to users in Germany. Amazon has a manual `Ctrl + D` bookmark tip. Gaming Deals contains no integration instructions. Existing custom messages require an explicit editor Reset to Default to adopt changed copy. Legacy Finanzberatung is not a required public channel; uncertain history remains MANUAL_REVIEW, with obsolete active mappings retired.

Legacy finanzberatung retirement is restricted to explicit owner setup Repair after replacement pins and mapping migration complete. `legacy_finance_service` shares read-only safety inspection with health: persisted identity, channel name/location, all stored resource dependencies, managed fingerprints, full message history, active/public/private archived threads and inspection permissions. Safe channels are REPAIRABLE; uncertainty is MANUAL_REVIEW with an exact reason. Sync/startup never call finance deletion. Historical tickets and audit/retired records remain stored. See [partner rollout and benefits-first copy](PARTNERS.md).

Bot grouping and private Affiliate Stats setup use verified `INSTANT_GAMING_BOT_ID`, `DEALGECKO_BOT_ID`, `JOCKIE_MUSIC_BOT_ID`, `PANCAKE_BOT_ID`. After restart, owner Repair creates/reuses groups, migrates existing private IG channel IDs and inserts gift-emoji Free Games under Deals. Configure the external targets manually after health/privacy checks; see [Discord setup](DISCORD_SETUP.md).

Known bot grouping requires no manual ID setup for DealGecko, Jockie Music or Pancake: public IDs are centralized in `config.py`; unset/zero optional overrides use those defaults. Preserve the existing Instant Gaming ID. Run `/server setup` → Repair / Setup → Confirm Repair, then `/server health`. The output lists all four exact-ID assignments; absent bots are warnings. See [bot grouping](DISCORD_SETUP.md).

Run `/server health` and owner `/server setup` → Repair to verify private AFFILIATE STATS purchases/buyer-ranking and the exact Instant Gaming bot identity. Parent and child access are diagnosed separately; normal members remain excluded and Gaming Bots receives no shared private grant. Reopen Instant Gaming `/config` after Repair and verify both destinations. For copied GoCDKeys partner links, use `/deals import-gocdkeys`: paste up to ten links in the modal, inspect the preview, edit uncertain titles, then Post New Deals. No prices are inferred. See [manual import and Codex input preparation](GOCDKEYS.md#manual-batch-import). For curated deals, use `/deals create partner:Amazon`, supply verified EUR prices and a product/affiliate URL, review the preview, then Post Deal. See [deal setup](DEALS.md). Automatic GoCDKeys lookup/backfill is unsupported after HTTP 403, even with the legacy GOCDKEYS_ENABLED flag; no Message Content Intent is required for manual deals. See [provider state](GOCDKEYS.md).

## Hidden Twitch beta

STREAMER_HUB_ENABLED=false and STREAMER_ROLE_SELECTION_ENABLED=false keep production inactive. Future Dev-only OAuth needs TWITCH_CLIENT_ID from a Public Twitch application; no callback/client secret is used. Explicit setup repairs managed streamer access and pins; health reports disabled as informational. See [configuration and Dev acceptance](STREAMER_HUB.md).

## Profile Settings acceptance

Restart the single local bot, run `/server health`, then `/server setup` → Repair →
Confirm Repair. Verify Profile Settings / Update Profile, Gaming Setup / Choose your
platforms, Interests & Notifications, and Missing something? / Suggest Role last.
About You, language controls and inactive playstyle copy must be absent. Repair retires
only the proven managed About You message and legacy language selections; it preserves
surviving message IDs and existing language-role memberships. Custom text remains
owner-controlled; use `/server pinned-messages` for any retained obsolete descriptions.

As a member, verify both Get Started and Update Profile show Gender → Age → Review →
Save only. Check preselection, Back, Cancel and Save; only personal roles may change.
Platform/notification buttons must toggle independently without opening the editor.
Games remain in choose-your-games. GamerHQ is English; no language question is asked.
See [profile settings](ROLE_SETTINGS.md) for migration and recovery details.

## Interactive boards and Community Events

After restarting the updated bot, run `/server health`, then owner `/server setup` → Repair. Repair creates/reuses `🎉・community-events` above tournaments and giveaways in EVENTS and maintains one canonical intro pin. It also repairs the selectors’ reaction/application-command permissions without allowing normal chat or threads. Check buttons/select menus and reactions using an ordinary member account. Existing event posts, IDs and private channels remain intact. See [permission modes](PERMISSIONS.md#read-only-modes).
