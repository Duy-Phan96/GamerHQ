# Instant Gaming: vier verwaltete Feeds

GamerHQ verwaltet diese Channels über gespeicherte IDs, Setup/Repair und
`/server instant-gaming`:

| Channel | Position | Mitglieder | Staff |
| --- | --- | --- | --- |
| 📰・gaming-news | an erster Stelle der vorhandenen 🤝 PARTNERS & BENEFITS-Kategorie | lesen, nicht schreiben | schreiben |
| 🔥・gaming-deals | direkt nach gaming-news | lesen, nicht schreiben | schreiben |
| 💸・purchases | 🔒 AFFILIATE STATS | unsichtbar | lesen; Schreiben wie Kategorie |
| 🏆・buyer-ranking | 🔒 AFFILIATE STATS | unsichtbar | lesen; Schreiben wie Kategorie |

Repair erzeugt bei Bedarf die private Kategorie AFFILIATE STATS; Bot-Gruppen werden durch den bestehenden Server-Repair verwaltet. Staff folgt der
vorhandenen GamerHQ-Regel: nicht verwaltete Rollen mit Administrator, Manage Server,
Manage Messages oder Moderate Members. Administratoren umgehen Discord-Overrides.
Explizite Freigaben normaler Rollen/Mitglieder in den internen Channels werden
geschlossen. Die öffentlichen Feeds erlauben keine Mitgliederposts oder Threads.

## Konfiguration und Bedienung

1. Den echten Bot über die [offizielle Bot-Seite](https://www.instant-gaming.com/en/discord/bot/)
   mit dem Partnerkonto verbinden und dessen Discord-User-ID prüfen.
2. `INSTANT_GAMING_BOT_ID` in der bestehenden privaten Environment-Konfiguration
   setzen und GamerHQ neu starten. Keine fest einprogrammierte ID, keine neue
   Bot-ID in SQLite. `0` bedeutet unkonfiguriert.
3. In Discord `/server instant-gaming`, danach `/server health` ausführen.
   Der Sync zeigt für jeden Channel Kategorie, Permissions, Pin und Botzugriff.
4. Mit einem normalen Mitglied und Staff Sichtbarkeit und mit dem externen Bot
   Posts, Links, Embeds und Anhänge testen; anschließend nochmals synchronisieren.
5. In der externen `/config` die tatsächlichen Channel-IDs zuordnen:
   **News** → gaming-news, **Marketing campaigns** → gaming-deals,
   **Purchase notification** → purchases, **Buyer ranking** → buyer-ranking.
   Bis zur Abnahme bleiben die externen Optionen disabled. Rollen-Mentions und
   automatische Threads bleiben disabled. Sprache/Währung sowie Affiliate-Zuordnung
   ownerseitig prüfen.

GamerHQ konfiguriert den externen Bot nicht und implementiert weder Scraping noch
Kauftracking. Keine Partner-Passwörter, API-Keys oder Käuferdaten gehören in die
GamerHQ-Config. Amazon-Automation bleibt Zukunftsarbeit über eine geeignete offizielle
API; kein Amazon-Scraping.

Fehlt die Bot-ID oder das Bot-Mitglied, entstehen trotzdem die vier korrekt
geschützten Channels. Sync loggt eine Warnung, Health zeigt den fehlenden Zugriff.
Bot-Overrides werden nur für einen vorhandenen externen Bot gesetzt. Er erhält
View Channel, Send Messages, Embed Links, Attach Files und Read Message History
in allen vier Channels. Administratorrechte sind dafür nicht erforderlich.
Nach einem ID-Wechsel/Deaktivieren: Neustart und Sync/Repair. Alte gezielte Grants
werden repariert; serverweite Administratorrechte können dadurch nicht entzogen werden.

## Bestehende Ressourcen und Persistenz

Alle Channels verwenden `managed_channel:<guild-id>:<channel-name>` in der
bestehenden settings-Tabelle. Gespeicherte IDs haben Vorrang vor Namen.
Fehlt die Ressource, wird ein eindeutiger vorhandener Namenskandidat übernommen,
sonst ein Ersatz erzeugt und gespeichert. Mehrdeutige Kandidaten führen zu
Manual Review; es werden keine Duplikate erstellt oder Channels zusammengelegt.

Gaming News und Gaming Deals werden mit ihren bestehenden IDs und ihrem Verlauf
in die vorhandene 🤝 PARTNERS & BENEFITS-Kategorie verschoben. Die verwaltete Reihenfolge ist News, Deals, Free Games, Amazon, AI Tools, Haushaltscheck.
Andere Channels behalten ihre relative Reihenfolge.
Die Kategorie-ID wird ebenfalls wiederverwendet; es entsteht keine neue Kategorie. Der Deals-Pin bleibt
unter `partner_message:<guild-id>:instant_gaming`; der
[bestehende Affiliate-Link](https://www.instant-gaming.com/?igr=gamer-0a9671a)
bleibt als Button erhalten. Die Partner-Navigation verlinkt weiterhin denselben
Channel. Partner-Repair/Sync verwenden dieselbe Position, denselben Text und
denselben Pin; es gibt keinen zweiten Nachrichtenverwalter mit abweichenden Defaults.

Die anderen Pins verwenden `instant_gaming_message:<guild-id>:<channel-name>`.
Alle vier nutzen das bestehende Managed-Message-Upsert. News und Deals bleiben
im öffentlichen Pin-Editor editierbar; bewusste Anpassungen überleben Sync.
Um die neuen Standardtexte statt eigener Texte zu übernehmen: bestätigtes
Reset to Default im Editor. Die internen Pins sind nicht als öffentliche Boards
im Editor registriert.

Setup/Repair und der gezielte Sync stellen gelöschte Channels/Pins wieder her und
reparieren Rechte. Startup aktualisiert nur Pins bereits registrierter Channels,
erzeugt keine Channels und ändert keine Rechte. Bei privater Rechteabweichung
werden interne Pins bis zum Repair nicht aktualisiert. Fremde Pins/Beiträge
bleiben bestehen. Zusätzliche verwaltet wirkende Pins werden in Health als
Manual Review gemeldet, nicht ungeprüft gelöscht.

## Standard-Pins

```markdown
# 📰 Gaming News

Stay up to date with gaming news, new releases and updates from the gaming world.

News in this channel may be posted automatically by our connected gaming services.
```

```markdown
# 🔥 Gaming Deals

Find current gaming deals, promotions and special offers here.

Affiliate / referral link
```

```markdown
# 💸 Instant Gaming Purchases

Internal channel for Instant Gaming purchase and affiliate notifications.

This channel is visible to staff only.
```

```markdown
# 🏆 Instant Gaming Buyer Ranking

Internal overview of Instant Gaming buyer rankings.

This channel is visible to staff only.
```

## Abnahme

- Sync zweimal: vier stabile Channel-IDs, vier stabile Pin-IDs, keine neuen Rollen.
- News/Deals öffentlich read-only; beide internen Channels für Mitglieder unsichtbar.
- Staff-Lesen im privaten Statistikbereich; Bot kann in allen vier Feeds posten.
- Neustart/Repair erhält News-/Deals-Anpassungen, Affiliate-Link, Verlauf und fremde Pins.
- Gelöschte Channels/Pins und falsche Overrides werden repariert.
- Health meldet fehlenden Bot, Kategorien/Rechte, doppelte Channels und doppelte Pins.

## Affiliate Stats migration and Free Games

Die internen Schlüssel `ig-purchases` und `ig-buyer-ranking` bleiben unverändert; Repair verschiebt die vorhandenen IDs nach AFFILIATE STATS und benennt nur die Channels um. Kategorie und Channels erlauben ausdrücklich der konfigurierten Instant-Gaming-Identität Zugriff. Die Gaming-Bots-Gruppenrolle gewährt keinen pauschalen privaten Zugriff. Vorhandene Administratorrechte fremder Bots müssen ownerseitig geprüft werden.

`🎁・free-games` liegt direkt unter Deals und gehört zu DealGecko. Keine News/Deals-Zusammenlegung, keine Affiliate-Formulierung im Free-Games-Standardpin. Details und Bot-Gruppierung: [Discord setup](DISCORD_SETUP.md).
