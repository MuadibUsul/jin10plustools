#!/usr/bin/env bash
# 从本机直接部署到云服务器（不依赖 GitHub Actions）。
#   bash deploy/deploy.sh
# 只上传服务端代码、模板、部署配置与 .env；在服务器上构建镜像并滚动更新。
set -euo pipefail

HOST="${PLUS_DEPLOY_HOST:-deploy@104.207.82.85}"
KEY="${PLUS_DEPLOY_KEY:-$HOME/.ssh/tline_vps_actions2}"
REMOTE_DIR="${PLUS_DEPLOY_DIR:-/home/deploy/plus}"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$HOST")

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "缺少根目录 .env"; exit 1; }

echo "==> 构建插件并放入网页下载目录"
(cd jin10-extension && npm run build --silent >/dev/null)
PY=jin10-server/.venv/Scripts/python; [ -x "$PY" ] || PY=python3
"$PY" - <<'ZIP'
import pathlib, zipfile
src = pathlib.Path("jin10-extension/dist")
with zipfile.ZipFile("jin10-server/app/web/plus-research-extension.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in src.rglob("*"):
        if p.is_file():
            z.write(p, pathlib.Path("plus-research-extension") / p.relative_to(src))
ZIP

echo "==> 打包"
tar -czf /tmp/plus-deploy.tgz \
  --exclude='jin10-server/.venv' --exclude='jin10-server/out' --exclude='jin10-server/data' \
  --exclude='jin10-server/.env' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='jin10-server/tests' --exclude='jin10-server/samples' \
  jin10-server deploy/docker-compose.yml

echo "==> 上传"
"${SSH[@]}" "mkdir -p '$REMOTE_DIR' && chmod 700 '$REMOTE_DIR'"
scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -q /tmp/plus-deploy.tgz "$HOST:$REMOTE_DIR/release.tgz"
scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -q .env "$HOST:$REMOTE_DIR/.env"
rm -f /tmp/plus-deploy.tgz

echo "==> 构建并启动"
"${SSH[@]}" bash -s -- "$REMOTE_DIR" <<'REMOTE'
set -euo pipefail
cd "$1"
chmod 600 .env
rm -rf jin10-server && tar -xzf release.tgz && rm release.tgz
mv deploy/docker-compose.yml docker-compose.yml && rmdir deploy
docker network inspect web >/dev/null 2>&1 || docker network create web
docker compose up -d --build
docker image prune -f --filter "label=com.docker.compose.project=plus" >/dev/null || true
for i in $(seq 1 30); do
  status=$(docker inspect plus-api --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}')
  [ "$status" = healthy ] && { echo "plus-api healthy"; exit 0; }
  sleep 3
done
docker compose logs --tail 50 api
exit 1
REMOTE
