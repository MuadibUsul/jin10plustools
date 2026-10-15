#!/usr/bin/env bash
set -euo pipefail
cd /home/deploy/plus-next
set -a
source .env
set +a
CHECK_IMAGE="${1:-$PLUS_NEXT_IMAGE}"
CHECK_DB="checks_$(date +%s)"
docker compose exec -T db createdb -U plus_next "$CHECK_DB"
trap 'docker compose exec -T db dropdb -U plus_next "$CHECK_DB"' EXIT
docker run --rm --memory=768m --cpus=1 --network plus-next_default \
    --entrypoint sh -v "$PWD:/workspace:ro" -w /workspace/jin10-server \
    -e PLUS_TEST_POSTGRES_URL="postgresql+psycopg://plus_next:$POSTGRES_PASSWORD@db:5432/$CHECK_DB" \
    -e PLUS_AI_WORKER_ENABLED=false "$CHECK_IMAGE" \
    -c 'python -m pip install --user pytest >/tmp/pytest-install.log 2>&1 && python -m pytest -q --tb=short -p no:cacheprovider'
