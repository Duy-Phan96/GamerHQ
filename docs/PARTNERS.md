# Support and Partners & Benefits

Support GamerHQ is a short overview in START HERE. Every offer has its own destination; energy and course remain separate messages within Strom & Gas.

```text
START HERE
└─ 💜・support-gamerhq
🤝 PARTNERS & BENEFITS
├─ 💜・direct-support
├─ 🛒・amazon
├─ ⚡・strom-gas
├─ 💶・finanzberatung
├─ 🎮・gaming-deals
└─ 🤖・ai-tools
```

Normal members can view/read/use buttons but cannot post or create threads. Staff and bot rights use the existing read-only helper. Setup preflights conflicting names/IDs and protected locations. No PayPal, payment system or inactive Donate button is introduced.

## Owner rollout

1. Deploy/restart the updated GamerHQ bot through the existing process.
2. Run `/server health` and review findings.
3. Owner: run `/server setup`, review the preview and choose Repair.
4. Verify seven public channels, eight managed pinned messages and the three private request buttons.
5. Repeat setup: channel/message IDs should remain stable.
6. Admin: `/server sync-support` refreshes adopted channels. Startup never creates missing partner structure.

## Safe migration

If only the old germany-services channel exists, owner repair reuses/renames it to strom-gas. Energy and course message IDs and channel history are preserved where possible. Finance is published in finanzberatung, pinned, and only then removed from the source. A snapshot in the existing settings table records source message IDs before destination IDs change; interrupted sends/pins/deletions can be retried. Pending three-topic reorder generations from the previous version are captured and retired through the same migration.

If strom-gas already exists separately, use it and remove only recognized managed source messages after all destinations are ready. The old channel is retained for MANUAL_REVIEW because uncached/manual/archived thread history may exist. Health and sync report this case. Unknown/user messages are never deleted. In the normal reuse path there is no remaining germany-services channel. Sources with missing stored message IDs use the existing bot-author/heading recovery convention over pins and the latest 100 messages.

The earlier seven-message support layout is still migrated: obsolete recorded offers are removed after all destinations are pinned. Existing support intro, Gaming Deals and AI Tools IDs are retained when reusable. The overview's former Amazon button is removed; Amazon has its own link button on its separate message.

Persistence remains in SQLite settings: existing per-topic `partner_message:<guild>:<section>` keys, new `direct` and `amazon` topic keys, and a resumable `partner_split:<guild>` journal. Ticket tables/types/lifecycle are unchanged. Strom & Gas ordering is energy then course; if deletion/recreation breaks order, the shared create/pin/atomic-switch/cleanup reorder flow restores it. Operate one bot instance per database; locks are process-local.

The empty obsolete SUPPORT GAMERHQ category may be removed after a fresh all-channel check. Nonempty categories and their child channels are retained. The separate private SUPPORT TICKETS category is unaffected.

## Final message texts

Each block is one managed message. Each overview list item contains its destination channel mention, resolved only from persisted managed IDs. There is no repeated footer. Missing or deleted destinations are omitted, with a brief setup notice; sync reports incomplete setup and health marks missing mappings REPAIRABLE. Owner setup creates/adopts the destinations. Buttons are attached only to their corresponding message.

### support-gamerhq / intro

```text
# 💜 Support GamerHQ

Wenn ihr GamerHQ unterstützen möchtet, findet ihr unter **PARTNERS & BENEFITS** verschiedene Möglichkeiten und Partnerangebote.

Dort findet ihr:

- 💜 <direct-support channel mention> — Direct Support
- 🛒 <amazon channel mention> — Amazon
- ⚡ <strom-gas channel mention> — Strom & Gas
- 💶 <finanzberatung channel mention> — Finanzberatung
- 🎮 <gaming-deals channel mention> — Gaming Deals
- 🤖 <ai-tools channel mention> — AI Tools

Einige Links sind Affiliate- oder Empfehlungslinks. Wenn ihr sie nutzt, unterstützt ihr GamerHQ direkt. Danke euch dafür 💜
```

Channel mentions above are documentation placeholders; the runtime renders real clickable mentions, not these labels or raw IDs. PARTNERS & BENEFITS stays bold plain text, not a category link. No buttons.

### direct-support / direct

```text
# 💜 Direct Support

Wenn du GamerHQ direkt unterstützen möchtest, findest du hier künftig die Möglichkeit dazu.

**Coming Soon**
```

No buttons.

### amazon / amazon

```text
# 🛒 Amazon

Du kannst GamerHQ unterstützen, indem du vor deinem normalen Amazon-Einkauf unseren Link verwendest.

Tipp: Speichere den Link als Lesezeichen in deinem Browser und nutze ihn einfach vor deinem nächsten Einkauf.
```

Link button: [🛒 Amazon öffnen](https://amzn.to/4dnxPXh).

### strom-gas / energy

```text
# ⚡ Strom & Gas

🇩🇪 Nur für Nutzer in Deutschland.

Du möchtest deinen Strom- oder Gasvertrag optimieren?

Über den Button erhältst du Zugang zu einem Netzwerk, über das du dir selbst einen passenden Strom- oder Gastarif auswählen kannst.

Brauchst du Unterstützung oder hast Fragen?
```

Buttons: [⚡ Strom & Gas starten](https://kundenportal.teleson.de/index.php?_url=register/karriere&reference=bFFQT1RPUHltMWVJb3REWXJDOWhwbzRNdXp5RTNhMUJWUkg4ckxZMHhVVjd5M0kvTWMvR3YrSkhCNWM4Z3ZiUnh2cDJSbFhEYUtjTHZKUWVlQWUrQnFPdWljOUpIbG5wc1drRk9KcXlhalU9); 🆘 Support anfragen → ENERGY_SUPPORT.

### strom-gas / energy_sales

```text
# 🎓 Strom & Gas Vertrieb

🇩🇪 Nur für Nutzer in Deutschland.

Du möchtest dich im Strom- & Gasvertrieb weiterbilden und selbst damit starten?

Dafür steht ein kompletter kostenloser Kurs zur Verfügung.

Über **Kurs anfragen** wird eine private Anfrage erstellt. Dort erhältst du Zugang zum kostenlosen Kurs.
```

Button: 🎓 Kurs anfragen → ENERGY_COURSE_REQUEST.

### finanzberatung / finance

```text
# 💶 Finanzberatung

🇩🇪 Nur für Nutzer in Deutschland.

Du möchtest deine Finanzen strukturiert überprüfen und langfristig besser aufstellen?

Ein persönlicher Finanzcheck kann helfen, Einnahmen und Ausgaben besser zu überblicken, bestehende Strukturen zu prüfen und finanzielle Ziele sinnvoll zu planen.

Ein besonderer Fokus kann dabei auf Vermögensaufbau, Investments und Immobilien liegen.

Eine feste Ansprechperson für Finanzfragen an der Seite zu haben, kann bei langfristigen Entscheidungen sehr hilfreich sein.
```

Button: 💬 Finanzcheck anfragen → FINANCE_REQUEST.

### gaming-deals / instant_gaming

```text
# 🎮 Gaming Deals

## Instant Gaming

Games & Deals

Use this link when buying games on Instant Gaming.

ℹ️ Affiliate Link
```

Link button: [🎮 Open Instant Gaming](https://www.instant-gaming.com/?igr=gamer-0a9671a).

### ai-tools / pixverse

```text
# 🤖 AI & Creator Tools

## PixVerse

AI Video Generation

Use PixVerse to create AI-generated videos and visual content.

ℹ️ Affiliate Link
```

Link button: [🤖 Open PixVerse](https://motivaiprivatelimited.sjv.io/c/7668488/3811144/49478).

## Existing private ticket lifecycle

ENERGY_SUPPORT, ENERGY_COURSE_REQUEST and FINANCE_REQUEST reuse support_tickets, per-user/per-type limits, staff access, audit logs, take/wait/close actions and restart recovery. Requests must match their current canonical message and topic channel. General support remains unchanged. Tickets are private to creator, authorized staff and bot (subject to Discord's normal administrator permissions).

Opening copy remains:

- Energy: “Deine private Support-Anfrage wurde erstellt. Beschreibe hier kurz, wobei du Unterstützung brauchst oder welche Fragen du hast.”
- Course: “Deine Anfrage wurde erstellt. Hier erhältst du Zugang zum kostenlosen Kurs.”
- Finance: “Deine private Anfrage wurde erstellt. Beschreibe hier kurz, welche Themen oder Ziele du besprechen möchtest.”

Each includes its existing heading, OPEN status, ticket metadata and actions. Closing retains readable history. No new transcript/export framework is introduced.

## Verification

Install test dependencies with `python -m pip install -r requirements-dev.txt`. Run `python -m pytest` or `python -m tools.test`. Both share temporary database isolation, disabled dotenv and blocked live Discord HTTP.

Manual rollout checks: inspect overview mentions and each button/URL; verify Direct Support is only Coming Soon; create each private request as a member; confirm staff access and unrelated-member denial; close/restart/retry; repeat setup and check IDs; inspect any MANUAL_REVIEW legacy channel before manually removing it. No live Discord deployment or verification is implied by offline tests.
