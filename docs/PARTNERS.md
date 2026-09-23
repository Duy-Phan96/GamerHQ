# Support and Partners & Benefits

```text
START HERE
├─ 🆘・need-support
└─ 💜・support-gamerhq
🤝 PARTNERS & BENEFITS
├─ 📰・gaming-news
├─ 🔥・gaming-deals
├─ 🎁・free-games
├─ 🛒・amazon
├─ 🤖・ai-tools
└─ 🇩🇪・haushaltscheck
```

Seven canonical messages across the overview and six partner channels. The overview, Gaming News, Amazon, Gaming Deals and AI Tools are English; only Haushaltscheck is German. Members can view/read/use buttons but cannot post or create threads. Staff and GamerHQ retain their existing access. The optional configured external Instant Gaming bot can post in gaming-deals; see [external setup](INSTANT_GAMING.md).

## Owner rollout and migration

The existing 🤝 PARTNERS & BENEFITS category keeps the managed order shown above:
News, Deals, Free Games, Amazon, AI Tools, Haushaltscheck by default. Explicitly adopted layout overrides are respected; other children retain their relative order.
Its existing channel/message IDs, customization and affiliate button are retained.
The overview still links it. Partner repair and Instant Gaming sync share the
same defaults and message key; neither moves it back to START HERE.
Purchases and Buyer Ranking are separate private AFFILIATE STATS feeds; see INSTANT_GAMING.md.

Back up SQLite, restart reviewed code, inspect `/server health`, then owner `/server setup` → Repair. Repeat Repair and compare identities. Existing recorded strom-gas, germany-services or finanzberatung channels may be reused, in that preference order, if no Haushaltscheck exists. Reused channel IDs/history remain intact. Name-only legacy channels are retained for review rather than renamed automatically.

A durable `household_migration:<guild>` journal captures recorded legacy message IDs and pending earlier reorder/split generations before renaming. Only after all replacement messages are pinned are recognized, bot-owned default energy/course/finance messages removed. Failures retain the journal for retry without duplicate replacement pins. Historical managed-content/audit records remain in SQLite, marked retired and excluded from the active editor. Completed migrations move old channel/message IDs from active settings to `retired_partner_channel` / `retired_partner_message` settings. This also cleans up mappings left by the previous release, while keeping renamed legacy channels discoverable for manual review. Ticket rows retain their original data and type.

Unknown/manual messages, customized legacy pins and uncertain fingerprints are preserved unchanged and flagged MANUAL_REVIEW. Only explicit owner `/server setup` → Repair can delete legacy finanzberatung. It requires a recorded managed identity, unchanged channel name, a non-protected location, completed household migration, no stored resource dependencies, sufficient inspection permissions, no active or archived threads, and full history containing only recorded bot-owned default messages (or no messages). Unknown/manual/customized content, unexpected attachments, dependency conflicts, API failures or uncertainty produce MANUAL_REVIEW with the exact reason. Health performs the same read-only check and reports REPAIRABLE when safe. Startup and sync never delete this channel. Other legacy channels remain for owner review. Old callbacks are no longer registered and ticket creation rejects old types. Retained buttons cannot open new requests. Owners must review/archive retained channels or remove obsolete custom pins themselves. This exception can leave old visible copy until review; preservation takes priority over destructive cleanup.

Existing custom overview/Gaming Deals copy remains customized. Repair updates defaults without overwriting it; use the editor's confirmed Reset to Default to adopt new navigation/copy. `/server sync-support` refreshes adopted boards and can resume a previously authorized migration, but never creates missing channels. Missing navigation mappings are omitted and flagged by health.

Support GamerHQ explains both direct support and support through useful partner/deal links. Owner Repair retires the recorded direct-support channel after publishing the overview, clears its active channel/pin mappings and retires editor state. Unknown/custom content, threads, dependencies or API failures retain the channel for manual review. No replacement is created.

## Final message texts

Affiliate disclosures are the last line of the same message body, immediately above the Discord link button. The Amazon shortcut is a manual browser instruction; no button creates a bookmark.

Mentions below are documentation placeholders. Runtime substitutes persisted Discord channel mentions once per bullet. Customized boards retain saved text until explicitly reset.

### support-gamerhq

```text
# 💙 Support GamerHQ

Want to support GamerHQ?

You can support us directly, or simply use one of our partner and deal links when you are planning to buy something anyway.

Every bit of support helps us keep GamerHQ running and improve the community. 💙

Check out our partner offers in **🤝 PARTNERS & BENEFITS**:

- 📰 <gaming-news channel mention> — Gaming News
- 🔥 <gaming-deals channel mention> — Gaming Deals
- 🛒 <amazon channel mention> — Amazon
- 🤖 <ai-tools channel mention> — AI Tools
- 🇩🇪 <haushaltscheck channel mention> — Haushaltscheck

No extra purchase is required — just use the links whenever they are useful to you.

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

### haushaltscheck

```text
# 🇩🇪 Haushaltscheck

Nur für Nutzer in Deutschland.

Viele Themen rund um Verträge, Tarife und laufende Kosten werden einem im Alltag kaum erklärt – und in der Schule meistens auch nicht.

Wenn du möchtest, kannst du deinen Haushalt kostenlos und unverbindlich prüfen lassen.

Dabei können zum Beispiel Bereiche wie:

- 🚗 KFZ
- ⚡ Strom & Gas
- 📄 laufende Verträge & Tarife

gecheckt werden.

Du bekommst mehrere passende Tarife übersichtlich zusammengestellt und als PDF zum Vergleichen.

So kannst du Preis und Leistung in Ruhe vergleichen und selbst entscheiden, ob und welches Angebot für dich sinnvoll ist.
```

Button: 🔍 Haushaltscheck anfragen → `HOUSEHOLD_CHECK_REQUEST`.

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

`HOUSEHOLD_CHECK_REQUEST` uses existing private tickets, per-user/per-type limits, Staff take/wait/close controls, audit and restart recovery. The entry checks canonical message/channel identity before creation. The user describes the areas in the private conversation; no extra category form is needed.

```text
# 🇩🇪 Haushaltscheck

Deine private Anfrage wurde erstellt.

Beschreibe hier kurz, welche Verträge oder Bereiche du prüfen lassen möchtest.

Status: OPEN
```

The real opening also includes existing ticket number, creator, type and assignment metadata. Creator, authorized Staff and GamerHQ can access it; unrelated members cannot (Discord administrators retain their platform permissions). Closing retains readable history. `GENERAL_SUPPORT` remains unchanged. Historical `ENERGY_SUPPORT`, `ENERGY_COURSE_REQUEST` and `FINANCE_REQUEST` tickets retain their type and lifecycle, but new requests of those types are rejected.

## Acceptance

Run `python -m pytest`. Live checks: compare IDs across two repairs; inspect all seven pins and exact links; create a household ticket with user A and confirm user B cannot view it; take/wait/close/restart; confirm customized pins survive; inspect each MANUAL_REVIEW finding. Verify external posting permission and attribution separately. Offline tests do not imply live Discord verification.

## Free Games default (member benefit, not affiliate promotion)

```text
# 🎁 Free Games

Free games and limited-time free-to-keep offers will be posted here automatically.

Keep an eye on the channel so you don't miss them. 🎮
```

DealGecko posts here using its configured user ID and scoped posting rights. It is separate from Instant Gaming's discounted/commercial offers in Gaming Deals and releases/news in Gaming News.
