#!/usr/bin/env bash
# Local transport for the same isolated release procedure used by GitHub Actions.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
git diff --quiet && git diff --cached --quiet || { echo 'Commit the verified release source first' >&2; exit 1; }
revision="$(git rev-parse HEAD)"
host="${PLUS_DEPLOY_HOST:-deploy@104.207.82.85}"
key="${PLUS_DEPLOY_KEY:-$(pwd)/../plus_deploy_ed25519}"
ssh_options=(-i "$key" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes)
archive="$(mktemp /tmp/plus-source.XXXXXXXX.tar)"
trap 'rm -f -- "$archive" "$archive.gz"' EXIT
git archive --format=tar HEAD jin10-server skills .dockerignore deploy/Dockerfile.release deploy/drain-worker.py deploy/runtime-wheels.py > "$archive"
(cd jin10-extension && npm run build)
python3 - <<'ZIP'
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
dist=Path('jin10-extension/dist');web=Path('jin10-server/app/web')
(web/'extension-version.json').write_text(json.dumps({'version':json.loads((dist/'manifest.json').read_text())['version']}))
with ZipFile(web/'plus-research-extension.zip','w',ZIP_DEFLATED) as bundle:
    for path in sorted(dist.rglob('*')):
        if path.is_file():bundle.write(path,path.relative_to(dist))
ZIP
tar --append -f "$archive" jin10-server/app/web/plus-research-extension.zip jin10-server/app/web/extension-version.json
gzip "$archive"
remote_archive="/tmp/plus-release-$revision.tar.gz"
remote_script="/tmp/plus-release-$revision.sh"
scp "${ssh_options[@]}" "$archive.gz" "$host:$remote_archive"
scp "${ssh_options[@]}" deploy/actions-release.sh "$host:$remote_script"
ssh -n "${ssh_options[@]}" "$host" bash "$remote_script" "$revision" "$remote_archive"
