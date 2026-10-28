#!/usr/bin/env bash
# Runs on the production host from the manual GitHub Actions deploy workflow.
set -Eeuo pipefail
umask 077

revision="${1:?commit SHA required}"
archive="${2:?release archive required}"
root=/home/deploy/plus
compose="$root/docker-compose.yml"
env_file="$root/.env"

[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid commit SHA' >&2; exit 1; }
[[ "$archive" == "/tmp/plus-release-$revision.tar.gz" ]] || {
  echo 'Unexpected release archive path' >&2
  exit 1
}
[[ -d "$root" && ! -L "$root" && -f "$compose" && -f "$env_file" ]] || {
  echo 'Existing PLUS installation is missing or not in the expected path' >&2
  exit 1
}
[[ -f "$archive" ]] || { echo 'Release archive is missing' >&2; exit 1; }
grep -Fq 'PLUS_RELEASE_IMAGE' "$compose" || {
  echo 'Existing PLUS compose file does not support an isolated release image' >&2
  exit 1
}
cd "$root"

compose_cmd=(docker compose --env-file "$env_file" -f "$compose" -p plus)
for service in api db brief-worker; do
  container_id="$("${compose_cmd[@]}" ps -q "$service")"
  [[ -n "$container_id" ]] || { echo "PLUS $service is missing" >&2; exit 1; }
  labels="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}:{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")"
  [[ "$labels" == "plus:$service" ]] || { echo "Unexpected $service container ownership" >&2; exit 1; }
  [[ "$(docker inspect -f '{{.State.Running}}' "$container_id")" == true ]] || {
    echo "PLUS $service is not running" >&2
    exit 1
  }
done
api_image_id="$(docker inspect -f '{{.Image}}' plus-api)"
worker_id="$("${compose_cmd[@]}" ps -q brief-worker)"
worker_image_id="$(docker inspect -f '{{.Image}}' "$worker_id")"
[[ "$api_image_id" == "$worker_image_id" ]] || {
  echo 'PLUS API and worker currently use different images; aborting release' >&2
  exit 1
}

release_image="plus-api:$revision"
PLUS_RELEASE_IMAGE="$release_image" "${compose_cmd[@]}" config --images | grep -Fxq "$release_image" || {
  echo 'PLUS compose did not select the release image' >&2
  exit 1
}

mkdir -p "$root/releases" "$root/backups"
release_dir="$(mktemp -d "$root/releases/$revision.XXXXXX")"
tar -xzf "$archive" -C "$release_dir"
rm -f -- "$archive"

echo "Building PLUS image $release_image"
docker build -f "$release_dir/jin10-server/Dockerfile" -t "$release_image" "$release_dir"

backup="$root/backups/plus-before-$revision-$(date -u +%Y%m%dT%H%M%SZ).dump"
"${compose_cmd[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup"
[[ -s "$backup" ]] || { echo 'Database backup is empty' >&2; exit 1; }
echo "PLUS database backup saved: $backup"

rollback_image="plus-api:rollback-$revision"
docker tag "$api_image_id" "$rollback_image"

rollback() {
  echo 'PLUS release failed; restoring previous API and worker image' >&2
  PLUS_RELEASE_IMAGE="$rollback_image" "${compose_cmd[@]}" up -d --no-deps --no-build api brief-worker || true
}

if ! PLUS_RELEASE_IMAGE="$release_image" "${compose_cmd[@]}" up -d --no-deps --no-build api; then
  rollback
  exit 1
fi

api_healthy=false
for _ in $(seq 1 60); do
  if [[ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' plus-api 2>/dev/null || true)" == healthy ]]; then
    api_healthy=true
    break
  fi
  sleep 3
done
if [[ "$api_healthy" != true ]]; then
  rollback
  echo 'PLUS API did not become healthy' >&2
  exit 1
fi

if ! PLUS_RELEASE_IMAGE="$release_image" "${compose_cmd[@]}" up -d --no-deps --no-build brief-worker; then
  rollback
  exit 1
fi
sleep 5
worker_id="$("${compose_cmd[@]}" ps -q brief-worker)"
if [[ -z "$worker_id" || "$(docker inspect -f '{{.State.Running}}' "$worker_id")" != true ]]; then
  rollback
  echo 'PLUS worker did not stay running' >&2
  exit 1
fi

echo "PLUS API and worker are running release $revision"
