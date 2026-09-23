# GoCDKeys comparison replies

Optional, only in the persisted `managed_channel:<guild>:gaming-deals` channel.
News, Free Games, other partner channels and LFG are not watched. No channels,
pins or existing Instant Gaming messages are edited. `cogs/gocdkeys.py` registers
an additive `on_message` listener and a scoped raw-edit listener. Historical messages are not replayed; edits fetch the current source message before reevaluation.

## Configuration

In the private environment set `GOCDKEYS_ENABLED=true`,
`GOCDKEYS_REFERRAL_CODE=kas66b` and the existing verified source identities. `SUPPORTED_DEAL_SOURCES` centrally allows Instant Gaming and DealGecko; their user IDs reuse bot configuration/stored mappings.
The existing `GUILD_ID` and managed deals mapping are required; there is no second
channel-ID configuration. Default is disabled. Enable **Message Content Intent**
for GamerHQ in the Discord Developer Portal before enabling/restarting the bot.
GamerHQ needs View Channel, Read Message History and Send Messages in deals.
No Administrator or new third-party credentials are required.

`/server health` reports watcher enabled/disabled, channel and referral/configuration
presence. It makes no web requests and does not certify remote availability or
the Developer Portal setting. Missing optional configuration is not core-critical.

## Matching contract and limitations

Only messages from configured Instant Gaming or DealGecko user IDs in the managed gaming-deals channel are eligible. Steam deals posted by either source use the same pipeline; arbitrary Steam-looking usernames, normal users, other bots and webhooks are ignored.
Extraction prefers embed titles, product fields, descriptions, linked text, then
message content. Price/discount/platform fields are not game titles. Normalization
removes price and PC/Steam suffix noise, retaining edition/DLC words.

The replaceable `GoCdKeysService.find_game_page` constructs a **candidate**, then
requires HTTP 200 HTML and an exact normalized product title and platform match.
It never publishes an unvalidated slug. The initial resolver supports observed
PC, PS4, PS5 and Xbox page/title formats; no platform label defaults to PC.
Explicit unsupported or ambiguous consoles, unexpected slugs, editions without
an exact page title, redirects, changed markup, timeouts and blocked requests are
skipped. This is conservative page validation, not a complete search API.
No anti-bot bypass or third-party search provider is used. A direct request from
the development environment returned HTTP 403; successful live delivery needs
owner verification from the deployment host. Do not treat mocked tests as proof
that GoCDKeys permits automated access there.

Observed reference formats: [PC](https://gocdkeys.com/buy-elden-ring-pc-cd-key),
[Xbox](https://gocdkeys.com/buy-elden-ring-xbox-one),
[PS5](https://gocdkeys.com/buy-ea-sports-fc-27-ps5).
Requests are limited to the exact HTTPS GoCDKeys host, without redirects, with
an 8-second timeout and 2 MB response limit. Results have a bounded in-memory
cache (success one hour, misses five minutes); fresh lookups are spaced by two
seconds, with busy bursts skipped. Arbitrary message URLs are never fetched.

Successful URLs replace the fragment with the configured referral. The reply
contains short text, one Compare Prices link button, neutral affiliate disclosure and a
source-message reference. Mentions and automatic embeds are disabled.

## Persistence and delivery failures

`db.init_db()` creates `processed_affiliate_deals` additively. The source message
ID is the primary key; rows record guild/channel, resolved referral URL, timestamp,
delivery status, response ID, normalized game and source key. Existing databases receive the two new text columns additively. An atomic `INSERT OR IGNORE` reserves delivery
**before** Discord send. Source processing is serialized within the process. Duplicate events, concurrent workers and restarts cannot
send twice for that ID. Do not remove rows as routine cleanup.

Delivery is deliberately **at most once**, not guaranteed delivery: a crash after
reservation can omit a reply; a timeout can leave a successfully delivered reply
with an `uncertain` status. Neither is retried automatically. Inspect Discord and
the private DB/logs manually rather than clearing claims blindly. Unresolved
lookups do not reserve a row. Logs never expose full message contents or secrets.

## Owner acceptance

1. Run `python -m pytest tests/test_gocdkeys.py -q` offline.
2. Enable the intent and private configuration, then deploy/restart using the
   existing [deployment workflow](../DEPLOY.md). Run `/server health`.
3. Let **Instant Gaming or DealGecko** publish a fresh supported paid deal
   in gaming-deals, e.g. Elden Ring PC with a positive price. A user/webhook impersonation is not valid.
4. If GoCDKeys is reachable and the page matches, expect exactly one compact
   reply. Check the button ends in `#ref=kas66b`, matches the game/platform and
   does not ping anyone. Verify the original deal and managed pin are unchanged.
5. Confirm user posts, untrusted bots and trusted sources outside deals cause no comparison. Test €0, FREE, Gratis and 100% OFF: no comparison. Free Games is always ignored; unknown prices/titles also cause no post.
6. Restart with the same DB: no historical reply is replayed. Duplicate event,
   concurrent delivery and uncertain-send behavior are covered by offline tests.
7. For a missing reply inspect private debug logs for `[gocdkeys]` lookup skips;
   HTTP 403 must remain a skip, not trigger a guessed link or access bypass.

Disable with `GOCDKEYS_ENABLED=false` and restart; existing comparisons and the
durable duplicate history remain intact.

## Paid/free routing and edits

Only confidently positive prices (currency amounts or structured price fields) are
eligible. Zero, FREE/Gratis/Kostenlos labels, free-to-keep and explicit 100% off
signals take precedence. Struck-through old prices and URL text are ignored.
Ambiguous/no price means skip; currency/edition/platform formats not recognized
are not guessed. DealGecko's external dashboard controls free-games versus paid
channel routing; GamerHQ does not scrape or automate that dashboard.

On paid → free/unknown or changed game title, the existing companion's button is
removed. On free → paid, an unprocessed source may get its first reply. A disabled
companion for the same normalized game is re-enabled in place only after provider
validation. Ownership, source reference and known companion content must match
before edits. Missing/manual/uncertain companions are not recreated. Original
source deletion does not automatically delete any Discord message. Do not reset
persisted claims to force retry. No repeated “supports GamerHQ” copy is added.

Repair grants the five public posting/read/embed/attachment rights to Gaming Bots
and DealGecko in gaming-deals, preserves Free Games, repairs parent visibility
where safe, and leaves unrelated overrides/private access intact. Parent posting
remains denied so this does not enable posting across all partner channels.
Unknown synced category children prevent automatic category changes and require
owner review. Health checks effective child rights and explicit parent denies.
