#!/usr/bin/env bash
# Local transport for the same isolated release procedure used by GitHub Actions.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
git diff --quiet && git diff --cached --quiet || { echo 'Commit the verified release source first' >&2; exit 1; }
revision="$(git rev-parse HEAD)"
host="${PLUS_DEPLOY_HOST:-deploy@104.207.82.85}"
key="${PLUS_DEPLOY_KEY:-$(pwd)/../plus_deploy_ed25519}"
local_python="${PLUS_LOCAL_PYTHON:-python3}"
ssh_options=(-i "$key" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 -o ServerAliveInterval=15)
archive="$(mktemp /tmp/plus-source.XXXXXXXX.tar)"
trap 'rm -f -- "$archive" "$archive.gz"' EXIT
git archive --format=tar HEAD jin10-server skills .dockerignore deploy/Dockerfile.release deploy/drain-worker.py deploy/runtime-wheels.py deploy/actions-release.sh deploy/remote-release.sh > "$archive"
(cd jin10-extension && npm run build)
"$local_python" - <<'ZIP'
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
dist=Path('jin10-extension/dist');web=Path('jin10-server/app/web')
(web/'extension-version.json').write_text(json.dumps({'version':json.loads((dist/'manifest.json').read_text(encoding='utf-8'))['version']}),encoding='utf-8')
with ZipFile(web/'plus-research-extension.zip','w',ZIP_DEFLATED) as bundle:
    for path in sorted(dist.rglob('*')):
        if path.is_file():bundle.write(path,path.relative_to(dist))
ZIP
tar --append -f "$archive" jin10-server/app/web/plus-research-extension.zip jin10-server/app/web/extension-version.json
gzip "$archive"
remote_archive="/tmp/plus-release-$revision.tar.gz"
remote_script="/tmp/plus-release-$revision.sh"
remote_dispatch="/tmp/plus-dispatch-$revision.sh"
scp "${ssh_options[@]}" "$archive.gz" "$host:$remote_archive"
ssh -n "${ssh_options[@]}" "$host" \
  "umask 077; tar -xOzf '$remote_archive' deploy/actions-release.sh > '$remote_script' && tar -xOzf '$remote_archive' deploy/remote-release.sh > '$remote_dispatch' && bash '$remote_dispatch' --start '$revision' '$remote_archive'"
deadline=$((SECONDS+1800))
while ((SECONDS<deadline)); do
  if state="$(ssh -n "${ssh_options[@]}" "$host" bash "$remote_dispatch" --status "$revision")"; then
    case "$state" in
      succeeded) echo "PLUS release $revision verified"; exit 0 ;;
      failed:*|interrupted) echo "Remote release $state; inspect /home/deploy/plus/releases/dispatch/$revision.log" >&2; exit 1 ;;
    esac
  else
    echo 'Release status connection unavailable; the server task continues' >&2
  fi
  sleep 20
done
echo "Remote release still requires verification; inspect /home/deploy/plus/releases/dispatch/$revision.log" >&2
exit 1
