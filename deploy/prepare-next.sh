#!/usr/bin/env bash
# Run on the server with a release archive. Does not change the original site or Caddy.
set -euo pipefail
umask 077
ARCHIVE="${1:?Usage: prepare-next.sh /absolute/path/release.tgz}"
ORIGINAL=/home/deploy/plus
TARGET=/home/deploy/plus-next
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="/home/deploy/plus-backups/$STAMP"
test -f "$ORIGINAL/docker-compose.yml"
test ! -e "$TARGET" || { echo 'Test site directory already exists; inspect it before updating.'; exit 1; }
mkdir -p "$BACKUP" "$TARGET"
docker ps --format '{{.Names}} {{.ID}} {{.Status}}' > "$BACKUP/containers-before.txt"
cp "$ORIGINAL/docker-compose.yml" "$BACKUP/docker-compose.original.yml"
cp "$ORIGINAL/.env" "$BACKUP/env.original"
tar -C "$ORIGINAL" -czf "$BACKUP/source-original.tgz" jin10-server
docker inspect plus-api --format '{{.Image}}' > "$BACKUP/original-image.txt"
docker image tag "$(cat "$BACKUP/original-image.txt")" "plus-api:before-next-$STAMP"
(
    cd "$ORIGINAL"
    docker compose exec -T db sh -c 'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$BACKUP/database.dump"
    docker compose exec -T api tar -C /data -czf - . > "$BACKUP/storage.tgz"
)
test -s "$BACKUP/database.dump"
test -s "$BACKUP/storage.tgz"
tar -tzf "$BACKUP/storage.tgz" > "$BACKUP/storage-manifest.txt"
tar -xzf "$ARCHIVE" -C "$TARGET"
cp "$TARGET/deploy/docker-compose.next.yml" "$TARGET/docker-compose.yml"
IMAGE="plus-next:review-$STAMP"
printf 'POSTGRES_PASSWORD=%s\nPLUS_JWT_SECRET=%s\nPLUS_NEXT_IMAGE=%s\n' \
    "$(openssl rand -hex 32)" "$(openssl rand -hex 48)" "$IMAGE" > "$TARGET/.env"
printf '%s\n' "$BACKUP" > "$TARGET/backup-path.txt"
cd "$TARGET"
# Legacy builder enforces per-build CPU and memory limits on this shared host.
DOCKER_BUILDKIT=0 docker build --memory=1g --cpu-period=100000 --cpu-quota=100000 \
    -t "$IMAGE" ./jin10-server > build.log 2>&1
docker compose config --quiet
docker compose up -d --wait db
docker compose exec -T db pg_restore -U plus_next -d plus_next --no-owner --no-acl < "$BACKUP/database.dump"
docker compose run --rm --no-deps --user root --entrypoint sh api \
    -c 'tar -xzf /dev/stdin -C /data' < "$BACKUP/storage.tgz"
docker compose up -d --wait api
docker compose exec -T api alembic current
# Only the cloned database is queried here. New schedules are disabled by default.
docker compose exec -T db psql -U plus_next -d plus_next -v ON_ERROR_STOP=1 \
    -c 'SELECT COUNT(*) AS enabled_schedules FROM brief_configs WHERE enabled;'
docker compose up -d brief-worker
docker compose ps
echo "Independent test site ready; backup: $BACKUP. Original site and Caddy unchanged."
