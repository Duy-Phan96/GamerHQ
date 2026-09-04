+# Deploy GamerHQ on a Linux VPS

GamerHQ runs well on a small always-on Linux VPS with Docker Compose. The code,
runtime configuration and live data are separate:

```text
/opt/gamerhq/app/
├─ .env
├─ compose.yaml
└─ runtime/
   ├─ data/gamerhq.db
   └─ backups/
```

The repository contains code only. `.env`, the live database and backups are
never committed or copied into a container image.

## 1. Prepare the release locally

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tools.production_preflight --db-path gamerhq.db
```

Commit the tested code and tag the exact release candidate.

## 2. Prepare the VPS

Install Git, Docker Engine and the Docker Compose plugin using your Linux
distribution or hosting provider documentation. Then:

```bash
sudo mkdir -p /opt/gamerhq/app/runtime/{data,backups}
sudo chown -R 10001:10001 /opt/gamerhq/app/runtime
cd /opt/gamerhq
git clone <YOUR_PRIVATE_REPOSITORY_URL> app
cd app
cp .env.example .env
chmod 600 .env
```

Fill `.env` with the production token and Discord IDs. Compose sets
`GAMERHQ_DB_PATH=/app/data/gamerhq.db`.

## 3. Move the existing live DB once

Stop the PC bot first so only one instance can use the token and DB. Create a
verified backup, then transfer that file over SSH/SFTP:

```powershell
.\.venv\Scripts\python.exe -m tools.backup_database C:\GamerHQ-Transfer\gamerhq.db
```

Place it at `/opt/gamerhq/app/runtime/data/gamerhq.db` and make it owned by
UID/GID `10001`. Never copy a database from a release archive.

## 4. Start and inspect

```bash
cd /opt/gamerhq/app
docker compose build --pull
docker compose run --rm gamerhq python -m tools.production_preflight --db-path /app/data/gamerhq.db
docker compose up -d
docker compose logs --tail=200 -f gamerhq
```

Verify `/game-admin database` in Discord and complete
`RELEASE_CHECKLIST.md`. Keep the PC instance stopped.

## 5. Backups

Create a consistent backup while the bot runs:

```bash
docker compose exec -T gamerhq python -m tools.backup_database
```

Schedule this daily with a systemd timer or cron, copy backups to a second
machine or object store, and periodically test a restore. Retention and deletion
are explicit host maintenance, never bot startup behavior.

## 6. Deploy an update

```bash
cd /opt/gamerhq/app
docker compose exec -T gamerhq python -m tools.backup_database
git fetch --tags
git checkout <TESTED_TAG>
docker compose build --pull
docker compose up -d
docker compose logs --tail=200 gamerhq
```

The bind-mounted runtime DB survives image rebuilds and code checkouts.

## Rollback

```bash
git checkout <PREVIOUS_KNOWN_GOOD_TAG>
docker compose build
docker compose up -d
```

Keep the live DB unless release notes explicitly document an incompatible
migration. See `ROLLBACK.md`.

