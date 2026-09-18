# Support and Partners & Benefits

Owner-managed public information boards. Support stays in START HERE; partner topics live in their own category.

```text
START HERE
└─ 💜・support-gamerhq
🤝 PARTNERS & BENEFITS
├─ 🇩🇪・germany-services
├─ 🎮・gaming-deals
└─ 🤖・ai-tools
```

Normal members can view/history/use buttons but cannot post or create threads. Existing staff/bot posting rights use the shared read-only helper. Owner setup checks conflicting IDs/names and protected locations before modifying partner resources. Unknown/manual channels and conversations remain untouched.

## Setup and updates

1. Deploy/restart the updated GamerHQ bot using the existing deployment procedure.
2. Run `/server health` and review findings.
3. Owner: run `/server setup`, inspect the preview and choose Repair.
4. Verify the structure, six separate pinned messages and three private request routes.
5. Run setup again: resource/message IDs should remain unchanged.
6. Admin: `/server sync-support` refreshes already configured channels. Before initial migration it requests owner setup; startup does not create missing partner structure.

The former combined/seven-message support board is migrated through the existing settings table. New per-section `partner_message:<guild>:<section>` IDs are persisted immediately. Only recorded bot-authored legacy messages with known headings are removed, after all destination messages are pinned. Failed deletion is retryable; unrelated mapped messages are retained with a warning. Interrupted previous support reorder generations are included in migration cleanup. Intro may reuse the prior bot-authored intro ID. Germany messages are ordered energy → course → finance; deletion of a middle message triggers the existing create/pin/atomic-switch/cleanup reorder mechanism, scoped to that channel. Recovery uses all pins and the last 100 messages and a process-local guild lock: operate one bot instance per database.

The obsolete empty SUPPORT GAMERHQ category is removed only after a fresh all-channel check. Nonempty categories and child channels are never deleted by this cleanup. Support Tickets is a separate private category and remains.

## Final public message texts

Discord renders each block below as one message with its own listed buttons. Support also appends clickable mentions of the three actual partner channels. No donation/PayPal button is present.

### support-gamerhq / intro

```text
# 💜 Support GamerHQ

GamerHQ ist kostenlos nutzbar.

Wenn du den Server unterstützen möchtest, findest du unter **PARTNERS & BENEFITS** verschiedene Partnerangebote und Empfehlungslinks.

Wenn du einen dieser Links nutzt, kann GamerHQ oder der jeweilige Partner eine Provision erhalten.

Für dich entstehen **keine zusätzlichen Kosten allein durch die Nutzung eines Empfehlungslinks**.

## 🛒 Amazon

Du kannst GamerHQ auch unterstützen, indem du vor deinem normalen Amazon-Einkauf unseren Link verwendest.

Tipp: Speichere den Link einfach als Lesezeichen im Browser und nutze ihn vor deinem nächsten Einkauf.

## 💜 Direct Support

Eine Möglichkeit zur direkten freiwilligen Unterstützung von GamerHQ folgt später.

**Coming Soon**
```

Link button: [🛒 Amazon öffnen](https://amzn.to/4dnxPXh).

### germany-services / energy

```text
# ⚡ Strom & Gas

Du wohnst in Deutschland und möchtest deinen Strom- oder Gasvertrag optimieren?

Über den Button erhältst du Zugang zu einem Netzwerk, über das du dir selbst einen passenden Strom- oder Gastarif auswählen kannst.

Brauchst du dabei Unterstützung oder hast Fragen?
```

Buttons: [⚡ Strom & Gas starten](https://kundenportal.teleson.de/index.php?_url=register/karriere&reference=bFFQT1RPUHltMWVJb3REWXJDOWhwbzRNdXp5RTNhMUJWUkg4ckxZMHhVVjd5M0kvTWMvR3YrSkhCNWM4Z3ZiUnh2cDJSbFhEYUtjTHZKUWVlQWUrQnFPdWljOUpIbG5wc1drRk9KcXlhalU9); 🆘 Support anfragen → ENERGY_SUPPORT.

### germany-services / energy_sales

```text
# 🎓 Strom & Gas Vertrieb

Du möchtest dich im Strom- & Gasvertrieb weiterbilden und selbst damit starten?

Dafür steht ein kompletter kostenloser Kurs zur Verfügung.

Über **Kurs anfragen** wird eine private Anfrage erstellt. Dort erhältst du Zugang zum kostenlosen Kurs.
```

Button: 🎓 Kurs anfragen → ENERGY_COURSE_REQUEST.

### germany-services / finance

```text
# 💶 Finanzcheck & Planung

Du möchtest deine Finanzen einmal strukturiert überprüfen und langfristig besser aufstellen?

Ein persönlicher Finanzcheck kann dabei helfen, Einnahmen und Ausgaben besser zu überblicken, bestehende Strukturen zu prüfen und finanzielle Ziele sinnvoll zu planen.

Ein besonderer Fokus kann dabei auf langfristigem Vermögensaufbau, Investments und Immobilien liegen.

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

## Private request lifecycle

ENERGY_SUPPORT, ENERGY_COURSE_REQUEST and FINANCE_REQUEST share the existing support_tickets storage, per-user/per-type limits, staff access, audit logs, take/wait/close actions and restart recovery. Requests are scoped to their canonical Germany Services message and channel. General support remains separate and unchanged. Finance tickets are private to creator, authorized staff and bot; Discord administrators retain Discord's normal permission bypass. No new transcript/export feature is claimed.

Opening messages:

- Energy: “Deine private Support-Anfrage wurde erstellt. Beschreibe hier kurz, wobei du Unterstützung brauchst oder welche Fragen du hast.”
- Course: “Deine Anfrage wurde erstellt. Hier erhältst du Zugang zum kostenlosen Kurs.”
- Finance: “Deine private Anfrage wurde erstellt. Beschreibe hier kurz, welche Themen oder Ziele du besprechen möchtest.”

Each includes its topic heading, OPEN status and the existing ticket metadata/actions. Closing preserves its history and disables creator posting.

## Verification

Install development dependencies with `python -m pip install -r requirements-dev.txt`. Run `python -m pytest` or the dependency-light existing runner `python -m tools.test`. Both use the same offline environment: temporary database, disabled dotenv and rejected live Discord HTTP calls.

Manual acceptance after repair: inspect all link targets, create each request as a member, verify unrelated-member denial and staff access, close a request, restart, retry buttons, rerun setup, delete a disposable managed message and sync again. Test pin/cleanup permission failure and retry in a test server. No live Discord verification or deployment is implied by offline tests.
