# Deploy GamerHQ on AlmaLinux

Deploy accepted GitHub `main` only, using Docker Compose. Development remains on `develop`; promotion is a separate owner review. The original baseline main lacks these deployment files. CI tests/builds but never deploys. The public repository can be cloned over HTTPS without a deploy key.

## Host preparation

Check `cat /etc/os-release` and install Docker Engine, Buildx and the Compose plugin using the RPM procedure appropriate for that AlmaLinux version. Consult [Docker's RPM instructions](https://docs.docker.com/engine/install/centos/) and the hosting provider for compatibility. Do not blindly replace packages on an existing host. Enable Docker at boot; verify `docker compose version` supports `up --wait`.

The target Network Solutions NVMe VPS has 50 GB disk. Leave space for images, backups and one database rehearsal copy. Keep SELinux enabled and preserve SSH access when configuring host updates/firewall. No bot port needs opening. Never put the VPS address, root password or token in Git.

As host administrator, on a new host with UID/GID 10001 unused:

```bash
sudo groupadd --gid 10001 gamerhq
sudo useradd --uid 10001 --gid gamerhq --create-home --shell /bin/bash gamerhq
sudo usermod -aG docker gamerhq
sudo install -d -o gamerhq -g gamerhq -m 700 /opt/gamerhq
sudo install -d -o 10001 -g 10001 -m 700 /opt/gamerhq/data /opt/gamerhq/backups
sudo systemctl enable --now docker
sudo -iu gamerhq
```

Inspect/reuse existing accounts instead of changing someone else's UID. Docker group access grants host-level control; restrict it to trusted operators. The application container runs as non-root 10001:10001 with dropped capabilities and a read-only root filesystem.

## Code and private configuration

As `gamerhq`:

```bash
umask 077
cd /opt/gamerhq
git clone --branch main https://github.com/Duy-Phan96/GamerHQ.git app
cp app/.env.example .env
chmod 600 .env
```

Edit `/opt/gamerhq/.env` privately with the real token, guild and selector IDs. Never overwrite it during updates. Optional `INSTANT_GAMING_BOT_ID` is explained in [external setup](docs/INSTANT_GAMING.md).

```text
/opt/gamerhq/
├─ .env                 private, mode 600
├─ app/                 clean main checkout
├─ data/gamerhq.db       persistent SQLite
├─ backups/             private verified snapshots
└─ .maintenance.lock    backup/update coordination
```

Compose mounts sibling data/backups to `/app/runtime/data` and `/app/backups`, overriding the DB path. The public seed remains inside the image at `/app/data`. Shared `:z` SELinux labels allow both bot and one-off maintenance containers to access these mounts. Fix ownership/labels on mount failure; do not disable SELinux. Use `docker compose config --quiet` to validate without printing expanded secrets.

## Transfer existing state and start

First pass `python -m pytest`, `python -m pip check` and `python -m tools.repository_audit --history`. Stop the PC bot and keep it stopped. From the local checkout, create a verified snapshot outside Git:

```powershell
.\.venv\Scripts\python.exe -m tools.backup_database C:\GamerHQ-Transfer\gamerhq.db
```

Transfer that snapshot privately over SSH/SFTP to `/opt/gamerhq/data/gamerhq.db`, owned by 10001:10001 with mode 600. Preserve the original. Never transfer an uncoordinated copy of a running SQLite file. Run only one bot against the guild/database.

As `gamerhq`:

```bash
cd /opt/gamerhq/app
bash scripts/update.sh --first
```

For a deliberately empty installation only, use `bash scripts/update.sh --first --fresh`. This explicitly permits a missing DB; preflight does not create it. The script builds, backs up an imported DB, rehearses migrations on a temporary copy beside the persistent data, checks directory access and starts Compose with a health wait. Preflight makes no Discord connection; final startup does.

Inspect `docker compose ps`, private `docker compose logs --tail=100 gamerhq`, Discord `/server health` and [release acceptance](RELEASE_CHECKLIST.md). `restart: unless-stopped` handles process exits/host reboot; unhealthy-but-running containers require investigation and are not automatically restarted. Do not start a second bot.

## Daily backups

As host administrator:

```bash
sudo install -m 644 /opt/gamerhq/app/deploy/gamerhq-backup.service /etc/systemd/system/
sudo install -m 644 /opt/gamerhq/app/deploy/gamerhq-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gamerhq-backup.timer
sudo systemctl start gamerhq-backup.service
sudo systemctl status gamerhq-backup.service
sudo systemctl list-timers gamerhq-backup.timer
```

The timer runs around 03:00 UTC daily with randomized delay and missed-run catch-up. It makes a consistent SQLite backup, verifies it and retains 14 dated daily snapshots. Repeats on the same UTC day retain that day's first verified snapshot. Only valid daily-named files are eligible for pruning; manual/pre-update backups remain. Monitor timer errors, disk space and manual snapshot accumulation. Keep encrypted off-host copies and complete a [restore drill](ROLLBACK.md).

## Normal updates

After the accepted release is on `origin/main`, run as `gamerhq`:

```bash
cd /opt/gamerhq/app
bash scripts/update.sh
```

The script requires the expected deployment path/origin, clean main and mode-600 `.env`. It locks maintenance, saves the prior image/commit, backs up SQLite, fast-forwards main, builds, preflights and waits for health. Errors stop execution. Inspect and roll back a failed update before retrying, so the saved previous image is not replaced by a failed candidate.

Bot logs rotate at three 10 MB files. No PostgreSQL or exposed service ports are needed. Image building, SELinux mounts, timer operation, restore rehearsal and live Discord acceptance remain owner-run host gates; Windows offline tests do not verify those gates.
