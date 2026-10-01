# External integrations

This is a routing reference, not a second setup guide.

Use `/server manage` → Integrations to select and confirm installed Instant Gaming,
DealGecko, Amazon Affiliate, Jockie or Pancake bot members. The existing `bot_member:<guild>:<name>`
settings take precedence over environment/bootstrap defaults. The legacy Instant
Gaming identity remains a final fallback and a record for revoking old access.
No secret is requested by this UI. Review structure/fixes separately to
apply scoped permissions. See [administration](PRODUCTION_OPERATIONS.md#server-manage).

| Integration | Actual scope and authority | Setup / health / failure |
| --- | --- | --- |
| Instant Gaming | External bot publishes; GamerHQ manages four channels, permissions and pins. Stored bot assignment preferred; INSTANT_GAMING_BOT_ID remains bootstrap fallback. Public News/Deals, private Purchases/Buyer Ranking. | Manage previews or specialized /server instant-gaming; health checks IDs, location, permissions, bot presence/access and pins/duplicates. Missing ID warns without blocking channel creation. [Full contract](INSTANT_GAMING.md). |
| Music Bots | External playback, no audio engine or music credentials in GamerHQ. Existing dedicated non-managed role stored as music_bots_role:<guild>. | /server music-bots-role; owner repair uses scoped music_bot_service access. Health reports missing/unsafe role and rights; never grant access to private Staff/ticket areas. [Permission contract](PERMISSIONS.md). |
| Twitch Streamer Hub | Hidden beta: approved-role Device OAuth, EventSub WebSockets and fixed live notices. Legacy profiles/following inactive; data and staff area tools retained. | Both streamer flags default false; TWITCH_CLIENT_ID for future Dev testing only. Persisted connections/session claims; no public listener. See [beta contract](STREAMER_HUB.md). |
| Amazon publisher | Optional external Amazon bot selected by exact Discord user ID. It may view Marketplace but receives posting/embed/file rights only in the managed `#amazon` channel; no Staff, Affiliate Stats or administrative grants. | `/server manage` → Integrations → Amazon Affiliate, then Structure → Fix Common Issues. Health reports missing/drifted scoped access. GamerHQ does not provide Amazon product-data automation here. |
| Affiliate links / Electricity | Public links/buttons and optional private electricity tickets; not merchant APIs or automated purchasing. | support_service and ticket_service; /server sync-support and owner Repair. Health/migration findings preserve uncertain/customized legacy content. [Partners](PARTNERS.md). |

Instant Gaming's external /config is not a GamerHQ command. Enabling campaigns, news, purchases or ranking and verifying attribution belongs to the owner. Preserve the existing affiliate URL/button and privacy gates.

Do not introduce scraping, third-party credentials, webhook servers or background jobs because a channel exists. Future integrations must explicitly define config, identity, visibility, setup, missing-config behavior, health, retry policy and tests. Reuse managed channels/messages rather than a parallel ID store. Amazon product-data/deal automation remains outside GamerHQ; only scoped Discord publisher access for the separately deployed Amazon bot is managed here. Twitch live integration is a hidden beta awaiting separate Dev-server acceptance.


Bot grouping uses verified optional user IDs: Instant Gaming/DealGecko → Gaming Bots, Jockie/Pancake → Music Bots. Free Games belongs to DealGecko; IG private destinations retain keys but move to AFFILIATE STATS. See [current Discord setup](DISCORD_SETUP.md).

GoCDKeys supports verified manually supplied links through `/deals create` and the bounded `/deals import-gocdkeys` preview/confirm workflow. Imports support DEAL comparison links and GIVEAWAY links, use fixed stored gaming-deals/giveaways targets, and deduplicate stored URLs. Giveaway links preserve supplied tracking parameters. Automated lookup/backfill is unsupported following HTTP 403 and performs no requests even when the legacy flag is enabled. Existing comparison mappings/messages remain intact. See [provider state](GOCDKEYS.md) and [curated deals](DEALS.md).
