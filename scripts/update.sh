#!/usr/bin/env bash
# Owner-invoked only. Never force-reset, merge, prune volumes or deploy develop.
set -euo pipefail
umask 077

main() {
    local first=0 fresh=0
    for arg in "$@"; do
        case "$arg" in
            --first) first=1 ;;
            --fresh) fresh=1 ;;
            *) echo 'Usage: ./scripts/update.sh [--first [--fresh]]' >&2; return 2 ;;
        esac
    done
    if (( fresh && ! first )); then echo '--fresh requires --first' >&2; return 2; fi
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    if [[ "$(pwd -P)" != /opt/gamerhq/app ]]; then echo 'Expected /opt/gamerhq/app; follow the deployment guide.' >&2; return 1; fi
    exec 9>/opt/gamerhq/.maintenance.lock
    flock -n 9 || { echo 'Another update/backup is running.' >&2; return 1; }
    [[ "$(git branch --show-current)" == main ]] || { echo 'Deploy only main.' >&2; return 1; }
    [[ -z "$(git status --porcelain)" ]] || { echo 'Checkout must be clean; preserve/review local changes first.' >&2; return 1; }
    [[ "$(git remote get-url origin)" == https://github.com/Duy-Phan96/GamerHQ.git ]] || { echo 'Unexpected origin; review manually.' >&2; return 1; }
    [[ -f ../.env && ! -L ../.env && "$(stat -c %a ../.env)" == 600 ]] || { echo 'Private ../.env must be a regular file with mode 600.' >&2; return 1; }
    [[ -d ../data && -d ../backups ]] || { echo 'Create private persistent directories first.' >&2; return 1; }
    docker compose version
    docker compose config --quiet
    if (( ! first )); then
        docker image tag gamerhq-bot:local gamerhq-bot:previous
        git rev-parse HEAD > /opt/gamerhq/previous-commit
        # The previous image backs up state BEFORE changing source or image.
        docker compose run --rm --no-deps gamerhq python -m tools.backup_database
    fi
    git fetch origin main --tags
    git merge-base --is-ancestor HEAD origin/main || { echo 'Local main is ahead or diverged; review manually.' >&2; return 1; }
    git merge --ff-only origin/main
    docker compose config --quiet
    docker compose build
    if (( first )) && [[ -f ../data/gamerhq.db ]]; then
        docker compose run --rm --no-deps gamerhq python -m tools.backup_database
    fi
    local -a preflight=(python -m tools.production_preflight --backup-dir /app/backups)
    if (( fresh )); then preflight+=(--allow-new); fi
    docker compose run --rm --no-deps gamerhq "${preflight[@]}"
    docker compose up -d --wait --wait-timeout 240
    docker compose ps
    printf 'Running release: '
    git rev-parse --short HEAD
    echo 'Next: inspect private logs, Discord /server health and the release checklist.'
}

main "$@"
