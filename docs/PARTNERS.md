# Support and Partners & Benefits

```text
START HERE
├─ 🆘・need-support
└─ 💜・support-gamerhq
🤝 PARTNERS & BENEFITS
├─ 💜・direct-support
├─ 🛒・amazon
├─ 🇩🇪・haushaltscheck
├─ 🎮・gaming-deals
└─ 🤖・ai-tools
```

Six canonical messages across the overview and five partner channels. The overview, Direct Support, Amazon, Gaming Deals and AI Tools are English; only Haushaltscheck is German. Members can view/read/use buttons but cannot post or create threads. Staff and GamerHQ retain their existing access. The optional configured external Instant Gaming bot can post in gaming-deals; see [external setup](INSTANT_GAMING.md).

## Owner rollout and migration

Back up SQLite, restart reviewed code, inspect `/server health`, then owner `/server setup` → Repair. Repeat Repair and compare identities. Existing recorded strom-gas, germany-services or finanzberatung channels may be reused, in that preference order, if no Haushaltscheck exists. Reused channel IDs/history remain intact. Name-only legacy channels are retained for review rather than renamed automatically.

A durable `household_migration:<guild>` journal captures recorded legacy message IDs and pending earlier reorder/split generations before renaming. Only after all replacement messages are pinned are recognized, bot-owned default energy/course/finance messages removed. Failures retain the journal for retry without duplicate replacement pins. Historical managed-content/audit records remain in SQLite, marked retired and excluded from the active editor. Completed migrations move old channel/message IDs from active settings to `retired_partner_channel` / `retired_partner_message` settings. This also cleans up mappings left by the previous release, while keeping renamed legacy channels discoverable for manual review. Ticket rows retain their original data and type.

Unknown/manual messages, customized legacy pins and uncertain fingerprints are preserved unchanged and flagged MANUAL_REVIEW. No legacy partner channel is automatically deleted, even when apparently empty: threads/history may exist. Old callbacks are no longer registered and ticket creation rejects old types. Retained buttons cannot open new requests. Owners must review/archive retained channels or remove obsolete custom pins themselves. This exception can leave old visible copy until review; preservation takes priority over destructive cleanup.

Existing custom overview/Gaming Deals copy remains customized. Repair updates defaults without overwriting it; use the editor's confirmed Reset to Default to adopt new navigation/copy. `/server sync-support` refreshes adopted boards and can resume a previously authorized migration, but never creates missing channels. Missing navigation mappings are omitted and flagged by health.

## Final message texts

Affiliate disclosures are the last line of the same message body, immediately above the Discord link button. The Amazon shortcut is a manual browser instruction; no button creates a bookmark.

Mentions below are documentation placeholders. Runtime substitutes persisted Discord channel mentions once per bullet. Customized boards retain saved text until explicitly reset.

### support-gamerhq

```text
# 💜 Support GamerHQ

If you'd like to support GamerHQ, check out the options and partner offers under **PARTNERS & BENEFITS**.

You'll find:

- 💜 <direct-support channel mention> — Direct Support
- 🛒 <amazon channel mention> — Amazon
- 🇩🇪 <haushaltscheck channel mention> — Haushaltscheck
- 🎮 <gaming-deals channel mention> — Gaming Deals
- 🤖 <ai-tools channel mention> — AI Tools

Some links are affiliate or referral links. Using them helps support GamerHQ. Thank you 💜
```

No buttons or duplicate footer.

### direct-support

```text
# 💜 Direct Support

If you'd like to support GamerHQ directly, a direct support option will be available here soon.

**Coming Soon**
```

No buttons. PayPal is not configured.

### amazon

```text
# 🛒 Amazon

Support GamerHQ when you shop on Amazon using our link.

Tip: Save the link as a browser bookmark with `Ctrl + D` and use it before your next purchase.

Affiliate link — using it supports GamerHQ 💜
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
# 🎮 Gaming Deals

Find current gaming deals, promotions and releases here.

Affiliate link — using it supports GamerHQ 💜
```

Button: [🎮 Open Instant Gaming](https://www.instant-gaming.com/?igr=gamer-0a9671a). External posts require owner configuration.

### ai-tools

```text
# 🤖 AI & Creator Tools

## PixVerse

AI Video Generation

Create AI-generated videos and visual content with PixVerse.

Affiliate link — using it supports GamerHQ 💜
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

Run `python -m pytest`. Live checks: compare IDs across two repairs; inspect all six pins and exact links; create a household ticket with user A and confirm user B cannot view it; take/wait/close/restart; confirm customized pins survive; inspect each MANUAL_REVIEW finding. Verify external posting permission and attribution separately. Offline tests do not imply live Discord verification.
