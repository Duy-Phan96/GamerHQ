# Support and Marketplace

```text
START HERE
├─ 🆘・need-support
└─ 💜・support-gamerhq
🛒 MARKETPLACE
├─ 📰・gaming-news
├─ 🔥・gaming-deals
├─ 🎁・free-games
├─ 🛒・amazon
├─ 🤖・ai-tools
└─ 🇩🇪・electricity
```

Seven canonical messages across the overview and six partner channels. The overview, Gaming News, Amazon, Gaming Deals and AI Tools are English; Electricity is also English and available to users in Germany. Members can view/read/use buttons but cannot post or create threads. Staff and GamerHQ retain their existing access. The optional configured external Instant Gaming bot can post in gaming-deals; see [external setup](INSTANT_GAMING.md).

## Owner rollout and migration

Explicit owner Repair renames the existing PARTNERS & BENEFITS category to 🛒 MARKETPLACE and haushaltscheck to electricity, retaining their Discord IDs/history. The internal category key `partners-benefits` and canonical pin key `partner_message:<guild>:household` deliberately remain stable. Active channel/adoption mappings move from `haushaltscheck` to `electricity` before the rename for safe retries. An old adopted display name is discarded for this explicit rename; other adopted properties remain. Conflicting mapped/named destinations require manual review, never a merge or duplicate. Intentionally removed channels remain removed.

Generated pin defaults update in place. Customized legacy pins remain preserved and health requests editor review or confirmed Reset to Default; the retired household action is disabled and unavailable for new editor actions. Old ticket records are never rewritten. Startup does not perform structural migration.

The existing 🛒 MARKETPLACE category keeps the managed order shown above:
News, Deals, Free Games, Amazon, AI Tools, Electricity by default. Explicitly adopted layout overrides are respected; other children retain their relative order.
Its existing channel/message IDs, customization and affiliate button are retained.
The overview still links it. Partner repair and Instant Gaming sync share the
same defaults and message key; neither moves it back to START HERE.
Purchases and Buyer Ranking are separate private AFFILIATE STATS feeds; see INSTANT_GAMING.md.

Back up SQLite, restart reviewed code, inspect `/server health`, then owner `/server setup` → Repair. Repeat Repair and compare identities. Existing recorded strom-gas, germany-services or finanzberatung channels may be reused, in that preference order, if no Electricity exists. Reused channel IDs/history remain intact. Name-only legacy channels are retained for review rather than renamed automatically.

A durable `household_migration:<guild>` journal captures recorded legacy message IDs and pending earlier reorder/split generations before renaming. Only after all replacement messages are pinned are recognized, bot-owned default energy/course/finance messages removed. Failures retain the journal for retry without duplicate replacement pins. Historical managed-content/audit records remain in SQLite, marked retired and excluded from the active editor. Completed migrations move old channel/message IDs from active settings to `retired_partner_channel` / `retired_partner_message` settings. This also cleans up mappings left by the previous release, while keeping renamed legacy channels discoverable for manual review. Ticket rows retain their original data and type.

Unknown/manual messages, customized legacy pins and uncertain fingerprints are preserved unchanged and flagged MANUAL_REVIEW. Only explicit owner `/server setup` → Repair can delete legacy finanzberatung. It requires a recorded managed identity, unchanged channel name, a non-protected location, completed household migration, no stored resource dependencies, sufficient inspection permissions, no active or archived threads, and full history containing only recorded bot-owned default messages (or no messages). Unknown/manual/customized content, unexpected attachments, dependency conflicts, API failures or uncertainty produce MANUAL_REVIEW with the exact reason. Health performs the same read-only check and reports REPAIRABLE when safe. Startup and sync never delete this channel. Other legacy channels remain for owner review. Old callbacks are no longer registered and ticket creation rejects old types. Retained buttons cannot open new requests. Owners must review/archive retained channels or remove obsolete custom pins themselves. This exception can leave old visible copy until review; preservation takes priority over destructive cleanup.

Existing custom overview/Gaming Deals copy remains customized. Repair updates defaults without overwriting it; use the editor's confirmed Reset to Default to adopt new navigation/copy. `/server sync-support` refreshes adopted boards and can resume a previously authorized migration, but never creates missing channels. Missing navigation mappings are omitted and flagged by health.

Support GamerHQ provides member-benefit-first navigation and one short neutral disclosure. Owner Repair retires the recorded direct-support channel after publishing the overview, clears its active channel/pin mappings and retires editor state. Unknown/custom content, threads, dependencies or API failures retain the channel for manual review. No replacement is created.

## Final message texts

Affiliate disclosures are the last line of the same message body, immediately above the Discord link button. The Amazon shortcut is a manual browser instruction; no button creates a bookmark.

Mentions below are documentation placeholders. Runtime substitutes persisted Discord channel mentions once per bullet. Customized boards retain saved text until explicitly reset.

### support-gamerhq

```text
# 💙 Support GamerHQ

Find useful deals, tools and services in **🛒 MARKETPLACE**.

Explore the offers below and choose what is useful to you.

- 📰 <gaming-news channel mention> — Gaming News
- 🔥 <gaming-deals channel mention> — Gaming Deals
- 🎁 <free-games channel mention> — Free Games
- 🛒 <amazon channel mention> — Amazon
- 🤖 <ai-tools channel mention> — AI Tools
- 🇩🇪 <electricity channel mention> — Electricity

Some links may be affiliate or referral links.
```

The existing pin is updated in place. No new payment link or duplicate pin is created.

### amazon

```text
# 🛒 Amazon

Use the link below when shopping on Amazon.

Tip: Save it as a browser bookmark with `Ctrl + D` so it's easy to find later.

Affiliate / referral link
```

Button: [🛒 Open Amazon](https://amzn.to/4dnxPXh).

### electricity

Germany-only electricity tariff comparison/request service.

```text
# 🇩🇪 Electricity

Available for users in Germany.

Looking for a better electricity tariff?

You can compare several suitable options through our partner and decide for yourself which one works best for you.
```

Button: ⚡ Compare Electricity Tariffs → `ELECTRICITY_REQUEST`.

### gaming-deals

```text
# 🔥 Gaming Deals

Find current gaming deals, promotions and special offers here.

Affiliate / referral link
```

Button: [🎮 Open Instant Gaming](https://www.instant-gaming.com/?igr=gamer-0a9671a). External posts require owner configuration.

### ai-tools

```text
# 🤖 AI & Creator Tools

## PixVerse

Create AI-generated videos and visual content with PixVerse.

Affiliate / referral link
```

Button: [🤖 Open PixVerse](https://motivaiprivatelimited.sjv.io/c/7668488/3811144/49478).

## Private requests

`ELECTRICITY_REQUEST` uses existing private tickets, per-user/per-type limits, Staff take/wait/close controls, audit and restart recovery. The entry checks canonical message/channel identity before creation. The user describes their electricity comparison request in the private conversation.

```text
# ⚡ Electricity Tariff Request

Your private request has been created.

Tell us briefly what you'd like to compare, and you'll receive suitable tariff options to review.

Status: OPEN
```

Creator, authorized Staff and GamerHQ can access it; unrelated members cannot (Discord administrators retain their platform permissions). Closing retains readable history. `GENERAL_SUPPORT` remains unchanged. Historical `HOUSEHOLD_CHECK_REQUEST`, `ENERGY_SUPPORT`, `ENERGY_COURSE_REQUEST` and `FINANCE_REQUEST` tickets retain their type and lifecycle; new requests of those types are rejected.

## Acceptance

Run `python -m pytest`. Live checks: compare IDs across two repairs; inspect all seven pins and exact links; create a electricity ticket with user A and confirm user B cannot view it; take/wait/close/restart; confirm customized pins survive; inspect each MANUAL_REVIEW finding. Verify external posting permission and attribution separately. Offline tests do not imply live Discord verification.

## Free Games default (member benefit, not affiliate promotion)

```text
# 🎁 Free Games

Free games and limited-time free-to-keep offers will be posted here automatically.

Keep an eye on the channel so you don't miss them. 🎮
```

DealGecko posts here using its configured user ID and scoped posting rights. It is separate from Instant Gaming's discounted/commercial offers in Gaming Deals and releases/news in Gaming News.
