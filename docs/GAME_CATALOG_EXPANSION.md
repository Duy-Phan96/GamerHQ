# Reviewed game catalog expansion

The [manifest](../data/game_catalog_expansion.json) records the owner's
70-title list. Existing titles and aliases are read from the target SQLite
database. The repository seed provides a reproducible comparison baseline.

## Repository seed preview

- 38 matches. Existing game IDs and all stored settings and resource
  mappings are preserved. Name variants include Overwatch → Overwatch 2,
  Minecraft (Java) → Minecraft, Civilization VII and Fall Guys.
- 32 proposed additions. They enter the existing table hidden, with the
  existing 🎮 icon fallback and no Discord role or channel. The display
  groups come from the current Host categories.
- The GTA decision is resolved. GTA V, GTA 5 and Grand Theft Auto V
  resolve to the existing GTA Online row. If the aliases are already present,
  the preview schedules no change. An older snapshot may show one alias-only
  update. A missing GTA Online row or competing match blocks apply.

Proposed additions against the seed:

- Deadlock
- Team Fortress 2
- Delta Force
- Battlefield 6
- Battlefield 2042
- Destiny 2
- Warframe
- Minecraft (Bedrock)
- No Man's Sky
- Sea of Thieves
- Red Dead Redemption 2
- Stardew Valley
- Sons of the Forest
- 7 Days to Die
- Raft
- Satisfactory
- Factorio
- Enshrouded
- Grounded 2
- World of Warcraft Classic
- The Elder Scrolls Online
- Black Desert Online
- Lost Ark
- New World
- RuneScape
- Old School RuneScape
- Path of Exile
- Elden Ring
- Baldur's Gate 3
- Legends of Runeterra
- Deep Rock Galactic
- R.E.P.O.

Run the preview against an authorized staging copy to establish actual game
IDs, role IDs, current channel IDs and legacy chat mappings. Seed-derived
counts are provisional. The tool reports saved channel links and never
infers links from Discord names. If a game has an existing unlinked channel,
review that separately in the established game-channel recovery flow.

## Preview and apply

The manifest uses the existing seed fields. Names and aliases match after
Unicode normalization, case folding and punctuation normalization.
Distinct edition and sequel tokens are retained. Shared aliases, conflicting
manifest rows and explicit review notes block apply. Deletion tombstones
prevent resurrection of previously retired entries.

On the approved environment, point the tool at its explicit database path.

```sh
python -m tools.game_catalog_import --database "$STAGING_DATABASE" \
  --manifest data/game_catalog_expansion.json
```

Review the JSON lists and saved resource mappings. Generate a fresh preview
against the same target and use its token for the approved maintenance
operation with the bot stopped. The tool takes the existing instance lock,
checks the exact plan again and verifies a new backup before inserting any
rows or appending approved aliases to the explicit target.

```sh
python -m tools.game_catalog_import --database "$STAGING_DATABASE" \
  --manifest "$REVIEWED_MANIFEST" --apply --review-token "$REVIEW_TOKEN" \
  --backup "$NEW_BACKUP_PATH"
```

Compare row counts and rerun preview after apply. Newly added titles should
resolve to the same IDs with zero planned inserts. Verify existing role and
channel mappings and reopen the existing selector. Catalog apply inserts hidden records and can append the approved aliases to
GTA Online. The existing game's identity and mappings are preserved. Approval of Discord resource provisioning is separate.

## PatchBot readiness

The [public PatchBot catalog](https://patchbot.io/games) lists a current feed
for 69 supplied titles when including the combined Warzone feed. It lists
Dead by Daylight under No Longer Supported. The current Overwatch feed is
distinct from the retired original. Java and Bedrock, the Warcraft editions,
the two Path of Exile games, the RuneScape variants, and Battlefield 6 and
2042 have separate listings. Review the combined Black Ops 7 + Warzone feed
before routing it to a Warzone channel. The Grand Theft Auto V feed listing
does not establish which feed is appropriate for the existing GTA Online channel.

Use PatchBot's official setup to select the game and the already approved
channel. Its published [developer API](https://patchbot.io/game-developers)
accepts updates from verified game developers. The importer has no PatchBot
dependency or subscription operation.
