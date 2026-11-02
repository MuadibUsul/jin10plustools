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

mkdir -p "$root/releases"
release_dir="$(mktemp -d "$root/releases/$revision.XXXXXX")"
tar -xzf "$archive" -C "$release_dir"
rm -f -- "$archive"

echo "Building PLUS image $release_image"
docker build -f "$release_dir/jin10-server/Dockerfile" -t "$release_image" "$release_dir"

# Requested 2026-11-02: no deployment backups or automatic rollback.
# Existing database/storage stay in place; only unused PLUS images are removed.
if ! PLUS_RELEASE_IMAGE="$release_image" "${compose_cmd[@]}" up -d --no-deps --no-build api; then
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
  echo 'PLUS API did not become healthy' >&2
  exit 1
fi

if ! PLUS_RELEASE_IMAGE="$release_image" "${compose_cmd[@]}" up -d --no-deps --no-build brief-worker; then
  exit 1
fi
sleep 5
worker_id="$("${compose_cmd[@]}" ps -q brief-worker)"
if [[ -z "$worker_id" || "$(docker inspect -f '{{.State.Running}}' "$worker_id")" != true ]]; then
  echo 'PLUS worker did not stay running' >&2
  exit 1
fi

# Persist the healthy tag before removing old tags, so the next compose restart
# cannot select a removed image. No other environment setting is changed.
python3 - "$env_file" "$release_image" <<'PY'
import os, re, sys, tempfile
from pathlib import Path
path=Path(sys.argv[1])
assert path.is_file() and not path.is_symlink()
original=path.read_text(encoding='utf-8')
line='PLUS_RELEASE_IMAGE='+sys.argv[2]
pattern=r'(?m)^(?:export\s+)?PLUS_RELEASE_IMAGE\s*=.*$'
updated=re.sub(pattern,line,original) if re.search(pattern,original) else original.rstrip('\n')+'\n'+line+'\n'
fd,name=tempfile.mkstemp(prefix='.env-release-',dir=path.parent)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(updated)
    os.chmod(name,path.stat().st_mode & 0o777)
    os.replace(name,path)
finally:
    if os.path.exists(name):os.unlink(name)
PY

while IFS= read -r image; do
  [[ -n "$image" && "$image" != *':<none>' ]] || continue
  image_id="$(docker image inspect -f '{{.Id}}' "$image")"
  running_ids="$(docker ps -q)"
  if [[ -n "$running_ids" ]] && docker inspect -f '{{.Image}}' $running_ids | grep -Fxq "$image_id"; then
    continue
  fi
  # No force: stopped-container references remain protected by Docker.
  docker image rm "$image" || echo "PLUS image still referenced; retained: $image" >&2
done < <(docker image ls --filter 'reference=plus-*' --format '{{.Repository}}:{{.Tag}}')

echo "PLUS API and worker are running release $revision"
