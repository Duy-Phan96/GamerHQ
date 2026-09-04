# Roll Back GamerHQ

Because code and runtime data are separate, a normal rollback changes the **code release**, not the live database.

1. Stop the bot.
2. Keep `C:\GamerHQ\data\gamerhq.db` in place.
3. Start the previous known-good release folder with the same `.env` / `GAMERHQ_DB_PATH`.
4. Verify `/game-admin database`.
5. Check Choose Your Games, one game area and `/lfg create`.

If a new release performed an incompatible database migration, restore the database backup made immediately before that release. Database migrations must be documented in the changelog before deployment.
