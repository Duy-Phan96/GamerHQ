#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /opt/gamerhq/app
exec 9>/opt/gamerhq/.maintenance.lock
flock -w 300 9
# One-off container also works if the bot has stopped. Never starts bot.py.
docker compose run --rm --no-deps gamerhq python -m tools.backup_database --daily /app/backups --retain 14 --prune
