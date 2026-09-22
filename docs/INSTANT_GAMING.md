# Optional Instant Gaming deal posts

GamerHQ provides the gaming-deals channel, its canonical intro/link and an optional posting permission. The external official bot supplies posts. Nothing is installed or enabled automatically; absence is an INFO health finding. No scraper or purchase tracking is implemented in GamerHQ.

## Owner configuration

1. Sign in to the GamerHQ Instant Gaming partner/affiliate account. Use the [official Discord bot page](https://www.instant-gaming.com/en/discord/bot/) to connect its bot to the correct server.
2. Verify the installed bot's Discord user ID. Set `INSTANT_GAMING_BOT_ID` in GamerHQ's private environment, restart GamerHQ and run owner `/server setup` → Repair. The ID is optional and defaults to `0`.
3. In the external bot's `/config`, select language/currency and target `🎮・gaming-deals`. Enable **Marketing campaigns**, which includes important game releases according to the official guide.
4. Leave **Purchase notification**, **Buyer ranking** and **News** disabled initially. Leave role mentions and automatic threads disabled.
5. Confirm the external account's affiliate attribution and inspect a posted link. The GamerHQ intro button must retain [the existing affiliate link](https://www.instant-gaming.com/?igr=gamer-0a9671a).
6. Verify ordinary members cannot post and external posts appear below the managed intro. Run Repair again to verify the configured bot keeps its channel access.

Grant the external bot only View Channel, Read Message History, Send Messages, Embed Links and Attach Files in gaming-deals. GamerHQ's permission repair denies management and everyone mentions in that channel. Do not give it Administrator or a broadly privileged role: a channel overwrite cannot neutralize Administrator. Review permissions requested by the external installation separately.

Changing the configured bot ID or setting it to `0`, followed by restart and Repair, removes the old explicit posting allowance through normal read-only repair. It does not uninstall the external bot or disable its external account settings.

No partner passwords, API keys or buyer data belong in GamerHQ configuration. Amazon automation is future work only, through a supported official affiliate/product API if available and compliant; no Amazon scraping is planned here.
