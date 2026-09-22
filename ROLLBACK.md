# Rollback and restore

Keep one bot instance and the PC bot stopped. Image rollback normally keeps the current DB. Restoring a snapshot loses subsequent state and does not roll back Discord messages/channels.

## Image rollback

Normal updates save `gamerhq-bot:previous` and `/opt/gamerhq/previous-commit`. First deployment has no previous image. Investigate a failed update before retrying; do not prune the saved image.

As `gamerhq`, in a dedicated shell:

```bash
cd /opt/gamerhq/app
exec 9>/opt/gamerhq/.maintenance.lock
flock -n 9
docker compose stop gamerhq
docker image inspect gamerhq-bot:previous --format '{{.Id}}'
cat /opt/gamerhq/previous-commit
```

Stop if lock acquisition or image verification fails. Confirm this was the healthy image. If runtime/Compose requirements changed, review compatible configuration first. With compatible current Compose:

```bash
docker image tag gamerhq-bot:local gamerhq-bot:failed
docker image tag gamerhq-bot:previous gamerhq-bot:local
docker compose up -d --no-build --force-recreate --wait --wait-timeout 240
docker compose ps
```

The checkout stays on main. Do not build immediately: that replaces the rolled-back image with the checked-out candidate. Record the running image privately, inspect health and make a reviewed corrective main release before updating again. Exit this shell to release the lock. Code rollback cannot undo Discord migrations; review the live structure separately.

## Offline restore drill

Replace `SELECTED.db` with a real verified snapshot filename. Use a fresh private destination; preserve the snapshot:

```bash
cd /opt/gamerhq/app
umask 077
mkdir /opt/gamerhq/restore-test
docker compose run --rm --no-deps -v /opt/gamerhq/restore-test:/restore:z gamerhq python -m tools.backup_database --source /app/backups/SELECTED.db /restore/gamerhq.db
docker compose run --rm --no-deps -v /opt/gamerhq/restore-test:/restore:z gamerhq python -m tools.production_preflight --db-path /restore/gamerhq.db --backup-dir /app/backups
```

This copies/verifies SQLite and rehearses migrations without Discord login or live DB replacement. Inspect expected records privately and test off-host backup recovery too. Use a new directory for later drills.

## Restore live state when necessary

For corruption, incompatible migration or explicitly accepted data loss:

1. Complete the drill and select a compatible image. Acquire the maintenance lock above, stop the bot and confirm no one-off maintenance container remains running.
2. Preserve the whole current data directory, including WAL/SHM files. For example, rename `/opt/gamerhq/data` to `/opt/gamerhq/data-before-restore` only after confirming the latter does not exist. Never mix old sidecars with a restored DB.
3. Recreate `/opt/gamerhq/data`, owner 10001:10001, mode 700. Copy the verified standalone drill DB into it as `gamerhq.db`, owner 10001:10001, mode 600. Keep the original snapshot and recovery directory.
4. Run `docker compose run --rm --no-deps gamerhq python -m tools.production_preflight --backup-dir /app/backups`. Stop on failure.
5. Run `docker compose up -d --no-build --force-recreate --wait --wait-timeout 240`. Inspect health, selectors, LFG and ticket privacy. Review stale Discord mappings before owner repair; never remove unknown resources to hide a mismatch.
6. Exit the maintenance shell to release the lock. Verify the next scheduled backup and retain recovery material until accepted.

For Windows local operation, stop the bot, preserve DB/sidecars together and run the reviewed code with the same private configuration. Never overwrite a running DB. Production VPS releases still originate from main.
