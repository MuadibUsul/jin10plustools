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

# Keep a bounded inventory of every running service outside the two PLUS
# application containers, including the existing PLUS database. Never inspect
# environment variables or modify these containers.
inventory="$release_dir/protected-containers.json"
python3 - "$inventory" <<'PY'
import json,subprocess,sys
from datetime import datetime,timezone
ids=subprocess.check_output(['docker','ps','-q'],text=True).split()
rows=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True)) if ids else []
protected={}
for row in rows:
    labels=row['Config'].get('Labels') or {}
    if labels.get('plus.validation')=='true':continue
    if labels.get('com.docker.compose.project')=='plus' and labels.get('com.docker.compose.service') in ('api','brief-worker'):continue
    state=row['State']
    started=datetime.fromisoformat(state['StartedAt'].replace('Z','+00:00'))
    looping=(row['RestartCount']>10 and state['ExitCode']!=0 and not state['OOMKilled']
             and (datetime.now(timezone.utc)-started).total_seconds()<120)
    protected[row['Id']]={'name':row['Name'],'image':row['Image'],'started':state['StartedAt'],
                         'restart_count':row['RestartCount'],'already_restarting':looping}
with open(sys.argv[1],'w') as stream:json.dump(protected,stream)
print('Protected running containers:',len(protected))
PY

echo "Building PLUS image $release_image"
base_image="$(docker inspect -f '{{.Config.Image}}' plus-api)"
mkdir -p "$release_dir/runtime-wheels"
docker run --rm --memory=256m --cpus=0.5 --network bridge --user 0 --entrypoint python \
  -v "$release_dir:/release" "$base_image" /release/deploy/runtime-wheels.py \
  /release/jin10-server/requirements.lock /release/runtime-wheels
docker build --network=none --build-arg "BASE_IMAGE=$base_image" -f "$release_dir/deploy/Dockerfile.release" -t "$release_image" "$release_dir"
drain_owner="$revision-$$"
release_stage=draining
finish_release() {
  result=$?
  trap - EXIT
  if [[ "$result" != 0 ]]; then
    if [[ "$release_stage" == worker_stopped ]]; then
      # No API replacement has begun: resume the same container, never roll back an image.
      if [[ "$(docker inspect -f '{{.State.Running}}' "$worker_id")" != true ]]; then docker start "$worker_id" >/dev/null || true; fi
      python3 "$release_dir/deploy/drain-worker.py" --worker "$worker_id" --owner "$drain_owner" --clear || true
    fi
    if [[ "$release_stage" == draining ]]; then
      python3 "$release_dir/deploy/drain-worker.py" --worker "$worker_id" --owner "$drain_owner" --clear || true
    fi
    docker run --rm --network none --volumes-from "$worker_id" --entrypoint python "$release_image" -c \
      "import json,sys; from app.config import get_settings; from app.db.models import utcnow; p=get_settings().storage_dir/'.brief-deployment-failure.json'; p.write_text(json.dumps({'revision':sys.argv[1],'stage':sys.argv[2],'at':utcnow().isoformat()}))" "$revision" "$release_stage" || true
    echo "PLUS release failed at stage $release_stage; no backup or rollback performed. Worker/API state requires verification." >&2
  fi
  exit "$result"
}
trap finish_release EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 "$release_dir/deploy/drain-worker.py" --worker "$worker_id" --owner "$drain_owner"
release_stage=worker_stopped

# Requested 2026-11-02: no deployment backups or automatic rollback.
# Existing database/storage stay in place; only unused PLUS images are removed.
release_stage=api_replacing
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
docker exec plus-api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready',timeout=10)"
docker exec plus-api python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; from alembic.runtime.migration import MigrationContext; from app.db.session import get_engine; c=get_engine().connect(); assert MigrationContext.configure(c).get_current_revision()==ScriptDirectory.from_config(Config('/srv/alembic.ini')).get_current_head(); c.close()"
docker exec plus-api python -c "from app.db.session import get_session_factory; from app.services.team_settings import migrate_tokens; db=get_session_factory()(); print('Migrated legacy encrypted provider tokens:',migrate_tokens(db)); db.close()"

release_stage=worker_replacing
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

python3 "$release_dir/deploy/drain-worker.py" --worker "$worker_id" --owner "$drain_owner" --clear
worker_ready=false
for _ in $(seq 1 12); do
  if docker exec plus-api python -c "import json; from datetime import datetime,timezone; from app.config import get_settings; p=json.loads((get_settings().storage_dir/'.brief-worker-heartbeat.json').read_text()); assert (datetime.now(timezone.utc)-datetime.fromisoformat(p['at'])).total_seconds()<20 and not p.get('draining')" 2>/dev/null; then
    worker_ready=true; break
  fi
  sleep 3
done
[[ "$worker_ready" == true ]] || { echo 'Worker heartbeat did not become ready; no rollback performed' >&2; exit 1; }

python3 - "$inventory" <<'PY'
import json,subprocess,sys
with open(sys.argv[1]) as stream:protected=json.load(stream)
for key,before in protected.items():
    row=json.loads(subprocess.check_output(['docker','inspect',key],text=True))[0]
    assert row['Image']==before['image'] and not row['State']['OOMKilled'], 'Protected service changed: '+before['name']
    if before.get('already_restarting'):
        assert row['State']['Running'] or row['State']['Restarting'], 'Previously unstable service stopped: '+before['name']
        print('Pre-existing external restart loop remains; service was not modified:',before['name'],
              'restarts before/after',before['restart_count'],row['RestartCount'])
    else:
        assert row['State']['Running'] and row['State']['StartedAt']==before['started'], 'Protected service restarted: '+before['name']
print('Protected container identities/images verified; stable services did not restart:',len(protected))
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

docker exec plus-api python -c "from app.config import get_settings; (get_settings().storage_dir/'.brief-deployment-failure.json').unlink(missing_ok=True)"
release_stage=verified
echo "PLUS API and worker are running release $revision"
