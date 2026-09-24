# GoCDKeys comparison replies

Optional, only in the persisted `managed_channel:<guild>:gaming-deals` channel.
News, Free Games, other partner channels and LFG are not watched. No channels,
pins or existing Instant Gaming messages are edited. `cogs/gocdkeys.py` registers
an additive `on_message` listener and scoped raw-edit/delete listeners. Historical messages are not replayed; edits fetch the current source message before reevaluation.

## Configuration

In the private environment set `GOCDKEYS_ENABLED=true`,
`GOCDKEYS_REFERRAL_CODE=kas66b` and the existing verified source identities. `SUPPORTED_DEAL_SOURCES` centrally allows Instant Gaming and DealGecko; their user IDs reuse bot configuration/stored mappings.
The existing `GUILD_ID` and managed deals mapping are required; there is no second
channel-ID configuration. Default is disabled. Enable **Message Content Intent**
for GamerHQ in the Discord Developer Portal before enabling/restarting the bot.
GamerHQ needs View Channel, Read Message History, Send Messages and Embed Links in deals. Reply/link buttons require no additional elevated permission.
No Administrator or new third-party credentials are required.

`/server health` reports watcher enabled/disabled, channel and referral/configuration
presence, the local Message Content intent and effective GamerHQ posting permissions. It makes no web requests and does not certify remote availability or
the Developer Portal setting. Missing optional configuration is not core-critical.

## Matching contract and limitations

Only messages from configured Instant Gaming or DealGecko bot IDs in the managed gaming-deals channel are eligible. Application/webhook posts require a Discord-supplied application ID matching one of those configured identities. Arbitrary webhooks, display-name impersonation, normal users, other bots and GamerHQ itself are ignored.
Extraction prefers embed titles, known Game/Title/Product fields, linked or labeled message content, then descriptions and conservative plain-content fallback. Unknown embed fields are not treated as titles. Price/discount/platform fields are not game titles. Normalization
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
   in gaming-deals, e.g. an embed titled Silent Hill: Townfall with empty message content. A user/arbitrary webhook impersonation is not valid.
4. If GoCDKeys is reachable and the page matches, expect exactly one compact
   reply. Check the button ends in `#ref=kas66b`, matches the game/platform and
   does not ping anyone. Verify the original deal and managed pin are unchanged.
5. Confirm user posts, untrusted bots and trusted sources outside deals cause no comparison. Free Games is always ignored. Missing price text does not block a confident title; an unresolved game causes no post.
6. Restart with the same DB: no historical reply is replayed. Duplicate event,
   concurrent delivery and uncertain-send behavior are covered by offline tests.
7. For a missing reply inspect private logs for `[gocdkeys]` lookup skips;
   HTTP 403 must remain a skip, not trigger a guessed link or access bypass.

Disable with `GOCDKEYS_ENABLED=false` and restart; existing comparisons and the
durable duplicate history remain intact.

## Paid/free routing and edits

The persisted gaming-deals channel defines the paid/reduced-deal scope; there is
no extra price/free classifier. Configure sources to route free offers to
free-games, which this listener never watches. DealGecko's free-games flow is
unchanged; GamerHQ does not scrape or automate its dashboard.

An unchanged normalized title does not repeat lookup or send. A changed title
updates the same mapped companion and stored URL/title only after provider
validation. An unresolved edit disables the old button; a later resolved edit can
restore it in place. Ownership, source reference and known companion content must
match before any edit/delete. Source deletion (including bulk deletion) removes
only its confidently mapped GamerHQ companion. A missing companion is treated as
already removed; permission failures retain the mapping. Deleted claims remain in
SQLite, preventing stale events/restarts from recreating a reply. Missing/manual/
uncertain companions are not recreated. No repeated “supports GamerHQ” copy is added.

## Failure audit

The previous implementation required `price_state(message) == 'paid'` before any
provider lookup. A supported embed with a clear title but no recognized positive
price therefore received no reply. The regression fixture uses **Silent Hill:
Townfall**, an empty content string and an embed title without price text; it now
reaches the existing provider. This reproduces a code-level blocker, not the exact
production incident: the original Discord payload and runtime logs were not
provided. Bot authors and embeds were already supported. Webhooks were previously
rejected unconditionally; now only trusted application identities qualify.

Other independent blockers remain observable: disabled configuration, missing
managed channel/source IDs, intent/permission problems, unresolved product pages,
HTTP 403/timeouts, and uncertain reply delivery. Health verifies local configuration
and permissions, not live provider reachability or the Developer Portal switch.
Info logs identify detection source/message ID, unresolved titles/products, HTTP
status and successful GoCDKeys delivery. Debug logs explain disabled/wrong-guild
or untrusted-source skips. No full payload, title text or referral URL is logged.

Repair grants the five public posting/read/embed/attachment rights to Gaming Bots
and DealGecko in gaming-deals, preserves Free Games, repairs parent visibility
where safe, and leaves unrelated overrides/private access intact. Parent posting
remains denied so this does not enable posting across all partner channels.
Unknown synced category children prevent automatic category changes and require
owner review. Health checks effective child rights and explicit parent denies.
