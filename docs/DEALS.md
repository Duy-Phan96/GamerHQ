# Curated Gaming Deals

`/deals create` extends the existing deal group. Only the configured server's owner
or a member with Administrator may create/confirm. Moderator, helper, Streamer
and voice-owner roles alone do not qualify. Authorization is checked at the
command, modal and every button, including immediately before posting.

1. Choose Amazon, Instant Gaming, GoCDKeys or Other. Optionally supply a public
   HTTPS image URL in the command (attachments are not part of this phase).
2. Enter title/product, current EUR price, optional regular EUR price, verified
   affiliate/product HTTPS URL and optional note.
3. Review the private preview and its fixed gaming-deals destination, then choose
   **Post Deal**, **Edit** or **Cancel**. No public post or DB write occurs on preview.
   Edit opens a new modal and invalidates the old confirmation. If that modal is
   dismissed or validation fails, start `/deals create` again. Sessions expire
   after five minutes; unfinished drafts do not survive a restart.

Prices accept decimal dots/commas with up to two decimal places, without currency
symbols or thousands separators. Current price may be zero. Optional regular price
must be positive and at least the current price. A rounded approximate percentage
is shown only for a genuine reduction; equal/missing regular prices show no discount.
GamerHQ does not invent/verify market prices or scrape Amazon, Instant Gaming or
GoCDKeys. The admin verifies supplied information and image usage before posting.

Public cards include partner-specific link buttons and neutral affiliate disclosure.
Amazon's existing general affiliate link remains unchanged; curated deals require
the actual product/affiliate link. Amazon regional domains and amzn.to/amzn.eu are
accepted, Instant Gaming and GoCDKeys require their respective domains. Other may
use a public HTTPS domain. Credentials, secret query parameters, local addresses,
IP literals, malformed/non-HTTPS URLs are rejected using the managed URL validator
plus partner restrictions. URLs are at most 512 characters. Links are not fetched
or redirect-verified; preview must be reviewed. See [GoCDKeys referral rules](GOCDKEYS.md).

The target uses only the persisted managed gaming-deals ID and is rechecked at
confirmation. No arbitrary destination, new channels, pins or changes to official
Instant Gaming posts. Free Games routing is unaffected. Public users cannot invoke
the editor or reuse another admin's session.

## Persistence and scope

The additive `curated_deals` table uses the existing DB and affiliate helper. Each
confirmed draft records its unique ID, guild/channel, actor, time, supplied metadata,
status and delivered message ID. Price metadata is stored as decimal strings;
discount is derived rather than duplicated. An atomic claim is written before send.
Double clicks, concurrent confirmation and replay of the same draft cannot send
twice, including after restart. A send timeout remains uncertain and is not retried
automatically: inspect Discord before making another draft. Different new drafts
are separate intentional posts; this is not content-based deduplication.

`/deals manage`, post-publication edit/removal/expiry and analytics are deferred.
No active/expired state is advertised yet. Stored message IDs provide a future
management foundation; this phase changes only unpublished drafts. Clicks and
conversions are not tracked. Runtime deal data stays private in the normal DB backup.

The Staff Command Guide includes `/deals` and uses managed pages under Discord's
2000-character limit. The original first-page mapping is retained. Repeated refresh
reuses page IDs; only confidently owned, mapped surplus pages can be retired.


## GoCDKeys link batches

For partner-dashboard links without manually entered prices, use
`/deals import-gocdkeys`. It shares authorization, managed target, URL validation,
SQLite table and at-most-once send helper with this editor. Its deterministic URL
claim additionally prevents duplicate imports across sessions/restarts. Import
metadata identifies `workflow=import-gocdkeys`; Amazon/Instant Gaming curated
cards and official bot posts are unaffected. Prior GoCDKeys curated records are
also checked before importing. `/deals create` remains an intentional per-draft
posting flow: it does not become a global URL deduplicator, so simultaneously
creating a separate curated draft for the same product is outside import dedupe.
See [manual import](GOCDKEYS.md#manual-batch-import) for exact URL/referral rules,
preview/title editing and Codex-assisted paste preparation.
