# GoCDKeys — manual links only

Automated product lookup returned **HTTP 403** in the owner's local environment.
There is currently no approved API/feed/search contract in this integration.
Automatic resolution, live enrichment and `/deals backfill` are therefore
**unsupported and disabled in code**, even if legacy `GOCDKEYS_ENABLED=true`
remains in the private environment. No scraper or bypass runs. The provider fetch
method makes no network requests. Health reports this optional limitation as INFO.
Bot startup does not depend on provider access or Message Content Intent.

## What works now

Use `/deals create partner:GoCDKeys` with a manually verified HTTPS GoCDKeys link,
product/title and prices. Review the private preview, then confirm Post Deal.
GamerHQ never constructs or guesses a product URL for a manual post. See the
[admin workflow](DEALS.md) for authorization, validation and durable delivery claims.

The existing `GOCDKEYS_REFERRAL_CODE` remains available (default `kas66b`). On
manually supplied URLs matching the existing supported exact-host product pattern
(`https://gocdkeys.com/buy-...-pc-cd-key`, `-ps4`, `-ps5`, `-xbox-one`, without a
query), the established helper applies `#ref=<configured code>`. The supplied path
is preserved. Other valid GoCDKeys HTTPS links retain their supplied query/fragment
unchanged: unsupported referral formats are not invented. Domain/path acceptance
does not prove the product, price or referral attribution; the owner verifies it.
Cards carry neutral affiliate disclosure and a GoCDKeys link button.

## Retained state and future approved access

The existing deterministic parser, URL/referral helpers and provider seam remain.
Historical `processed_affiliate_deals` claims and comparison messages are preserved;
no automatic edit/delete/repost or DB reset occurs in unsupported mode. The legacy
backfill command clearly explains the block and points to manual creation. The
legacy opt-in alone cannot re-enable requests. Existing pipeline tests with mocked
approved-provider behavior preserve its contracts, while default-mode tests prove
that no HTTP calls or automatic posts occur.

Official Instant Gaming messages remain the primary automated gaming-deals source.
DealGecko Free Games remains separate and receives no comparison enrichment.
Manual GamerHQ posts do not trigger the automatic listener.

Owner action: contact GoCDKeys about a partner API, product feed, search endpoint,
documented URL/slug format and an approved Discord automation approach. No email
is sent by GamerHQ. Do not include credentials or private correspondence in the
repository. A future integration must use documented approved access and new tests;
a VPS move, User-Agent change or guessed URL is not a supported solution to 403.

## Offline and live acceptance

Run `python -m pytest tests/test_curated_deals.py tests/test_gocdkeys.py` offline.
Then restart the one local bot instance, run `/server health`, and create one
manual preview using a verified GoCDKeys URL. Check the exact destination, game,
price and referral before confirmation. Expect one GamerHQ post, no changes to
third-party messages and no provider lookup. Mocked tests do not certify provider
availability or affiliate attribution.
