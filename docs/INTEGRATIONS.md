# External integrations

This is a routing reference, not a second setup guide.

| Integration | Actual scope and authority | Setup / health / failure |
| --- | --- | --- |
| Instant Gaming | External bot publishes; GamerHQ manages four channels, permissions and pins. INSTANT_GAMING_BOT_ID comes from config.py. Public News/Deals, private Purchases/Buyer Ranking. | /server instant-gaming or owner Repair; health checks IDs, location, permissions, bot presence/access and pins/duplicates. Missing ID logs/warns without blocking channel creation. [Full contract](INSTANT_GAMING.md). |
| Music Bots | External playback, no audio engine or music credentials in GamerHQ. Existing dedicated non-managed role stored as music_bots_role:<guild>. | /server music-bots-role; owner repair uses scoped music_bot_service access. Health reports missing/unsafe role and rights; never grant access to private Staff/ticket areas. [Permission contract](PERMISSIONS.md). |
| Streamer/Twitch-facing UI | cogs/streamer.py stores profiles/channel names, follower roles, optional areas and owned rooms. Twitch link/verification fields exist; automatic Twitch live polling/promotion is not implemented. | /streamer profile/setup/area/audience/channels/voice; own DB mappings and startup views. No Twitch API secret or OAuth setup is configured here. Do not present the profile feature as working live detection. |
| Affiliate links / Haushaltscheck | Public links/buttons and optional private household-check tickets; not merchant APIs or automated purchasing. | support_service and ticket_service; /server sync-support and owner Repair. Health/migration findings preserve uncertain/customized legacy content. [Partners](PARTNERS.md). |

Instant Gaming's external /config is not a GamerHQ command. Enabling campaigns, news, purchases or ranking and verifying attribution belongs to the owner. Preserve the existing affiliate URL/button and privacy gates.

Do not introduce scraping, third-party credentials, webhook servers or background jobs because a channel exists. Future integrations must explicitly define config, identity, visibility, setup, missing-config behavior, health, retry policy and tests. Reuse managed channels/messages rather than a parallel ID store. Amazon automation and full Twitch live integration remain unimplemented.


Bot grouping uses verified optional user IDs: Instant Gaming/DealGecko → Gaming Bots, Jockie/Pancake → Music Bots. Free Games belongs to DealGecko; IG private destinations retain keys but move to AFFILIATE STATS. See [current Discord setup](DISCORD_SETUP.md).
