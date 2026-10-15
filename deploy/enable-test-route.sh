#!/usr/bin/env bash
# Add /test/ only after the independent PLUS backend is healthy.
set -euo pipefail
umask 077
cd /home/deploy/plus-next
BACKUP=$(cat backup-path.txt)
docker compose exec -T api python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/api/health').status == 200"
cp /opt/caddy/Caddyfile "$BACKUP/Caddyfile.before-test"
docker exec caddy-caddy-1 wget -qO- http://127.0.0.1:2019/config/ > "$BACKUP/caddy-runtime.json"
docker exec caddy-caddy-1 caddy adapt --config /etc/caddy/Caddyfile > "$BACKUP/caddy-from-file.json"
python3 - "$BACKUP" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
if json.loads((root/'caddy-runtime.json').read_text()) != json.loads((root/'caddy-from-file.json').read_text()):
    raise SystemExit('Active Caddy configuration differs from disk; inspect before reloading')
PY
python3 deploy/add-test-route.py "$BACKUP/Caddyfile.before-test" Caddyfile.test-candidate
docker cp Caddyfile.test-candidate caddy-caddy-1:/tmp/Caddyfile.plus-test
docker exec caddy-caddy-1 caddy validate --config /tmp/Caddyfile.plus-test --adapter caddyfile
cmp -s /opt/caddy/Caddyfile "$BACKUP/Caddyfile.before-test" || { echo 'Caddy file changed concurrently'; exit 1; }
cp Caddyfile.test-candidate /opt/caddy/Caddyfile
if ! docker exec caddy-caddy-1 caddy reload --config /tmp/Caddyfile.plus-test --adapter caddyfile; then
    cp "$BACKUP/Caddyfile.before-test" /opt/caddy/Caddyfile
    exit 1
fi
echo 'Only /test/ now routes to plus-next-api; the original site remains on plus-api.'
