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

Both manual commands accept the exact gocdkeys.com/gocdkeys.de hosts (with optional www). Conflicting or duplicate referral values are rejected rather than overwritten.

The existing `GOCDKEYS_REFERRAL_CODE` remains available (default `kas66b`). On
manually supplied URLs matching the existing supported exact-host product pattern
(`https://gocdkeys.com/buy-...-pc-cd-key`, `-ps4`, `-ps5`, `-xbox-one`, without a
query or fragment), the established helper applies `#ref=<configured code>`. The supplied path
is preserved. Already-correct query/fragment referral links remain unchanged. Other valid
GoCDKeys HTTPS links in `/deals create` retain their supplied query/fragment
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


## Manual batch import

**Manual partner-link import: supported. Automatic product lookup: disabled,
awaiting approved official access.** No login, cookies, account credentials,
dashboard automation, page requests, scraping or HTTP-403 bypass is used.

Run `/deals import-gocdkeys`. Its modal accepts **1–10 non-empty lines**, one URL
per line, **4000 characters total** and 512 per URL. Surrounding whitespace and
blank lines are ignored. Owner/Administrator access in the configured server is
rechecked on the command, modal, buttons and confirmation; ordinary Staff without
Administrator is not authorized. The target is only the stored gaming-deals ID.

Accepted product routes begin `/buy-` or `/kaufen-` with a lowercase ASCII slug.
Only HTTPS on `gocdkeys.com`, `www.gocdkeys.com`, `gocdkeys.de` or `www.gocdkeys.de`
is allowed, with no nonstandard port, credentials or secret query parameters.
Unrelated domains, dashboard/search/root/unknown routes, malformed URLs and
non-HTTPS schemes are invalid/manual-review. This syntactic acceptance is not
remote verification: use links copied from your partner dashboard.

Referral handling uses the configured code (currently `kas66b`):

- An existing single `?ref=kas66b` or `#ref=kas66b` is preserved exactly, including
  other URL parameters. Conflicting codes, empty values, duplicate parameters or
  ambiguous casing require manual review; they are never silently replaced.
- A missing referral is appended as the established `#ref=...` **only** for exact
  `https://gocdkeys.com/buy-...` product URLs accepted by the existing helper,
  without query/fragment. No new parameter format is inferred.
- Missing referral on `.de`, `www` or another unsupported variant is not guessed.
  Copy the complete partner link and import it again. A VPS is not required.

The private preview counts new links, already posted, retained delivery claims,
in-batch duplicates and invalid entries. It displays three entries per page with
links to review. No writes or public posts occur yet. Titles are suggestions made
solely from the supplied slug: anchored buy/kaufen prefixes and recognized terminal
platform/key suffixes are removed; Roman numerals stay uppercase and `marvels`
is displayed as `Marvel's`. Edition/platform identity is retained for deduplication.
Numeric, generic or unrecognized suffix cases require **Edit Titles** before Post
New Deals becomes available. All suggested titles remain editable; no missing
name, release date, availability, price or saving is invented.

Edit Titles opens a modal with `line number | title` for every new entry, plus
optional `line number | note` lines. Titles allow 1–200 characters, notes 1–120.
For example, an owner-supplied `Coming soon` note is allowed. The edited batch
returns to preview and invalidates its prior confirmation. Invalid edits return
to the unchanged preview. Cancel posts nothing. Sessions expire after five minutes
and unfinished drafts are memory-only. Dismissing an edit modal requires starting
an import again; no existing delivery records are lost.

**Post New Deals** sends one message per new URL, serialized with at least one
second between delivery attempts in this bot process (Discord's client additionally
handles API rate limits). Each message contains:

```text
🎮 **Grand Theft Auto VI**

Compare current game-key prices on GoCDKeys.

Affiliate / referral link
```

There is one **💰 Compare Prices** link button using the exact validated partner
URL, plus any manually supplied short note. Mentions and automatic URL embeds
are disabled. No guaranteed best-price/savings/availability language is added.
The final private result lists posted, skipped, invalid and uncertain entries.

### Persistent duplicate protection

The existing `curated_deals` table stores partner `gocdkeys`, workflow
`import-gocdkeys`, source/validated/normalized URL, title/note, creator/time, target,
message ID and delivery status. No schema change or separate analytics system.
The primary key is a guild-scoped SHA-256 identity of the normalized URL. An atomic
claim precedes send, preventing repeated or concurrent imports across restarts.

Normalization is for **identity only**, not the posted button: lowercase host,
optional www/default port and trailing slash are normalized; ref/utm/fbclid/gclid
tracking is ignored, remaining query/fragment parameters are sorted. Country
hosts, product path, edition and platform remain distinct. Existing GoCDKeys
curated records are checked as well. Uncertain/reserved/deleted delivery claims
are retained, never automatically retried; a failed partial batch can be previewed
again safely. Do not clear the DB to retry. Different guilds remain independent.
Already posted status reflects the stored delivery, not a fresh fetch of its
Discord message. Deleted posts are not recreated. `/deals manage`, expiry and
post-publication editing remain future enhancements.

### Codex-assisted workflow

Paste your public partner URLs into Codex and ask **“Prepare these for GamerHQ.”**
No dashboard login or credentials are needed. Codex can remove duplicate lines
and return the command plus a separate exact modal input block, flagging uncertain
links rather than inventing formats:

1. Run `/deals import-gocdkeys` in Discord.
2. Paste the prepared URL-only block into the modal, one link per line.
3. Review every preview page, correct titles if needed, then confirm Post New Deals.

Do not paste the slash command itself into the URL field. Codex prepares the input;
GamerHQ posts only after your confirmation. Plain text paste is supported; file or
HTML/dashboard export parsing is not part of this phase.
