#!/usr/bin/env bash
# 一键启动网页版（后端 8765 + 前端 5173，均监听 0.0.0.0 供预览代理访问）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

if ! python3 -c "import uvicorn" >/dev/null 2>&1; then
  pip install -q -r "$ROOT/backend/requirements.txt"
fi
if [ ! -x "$ROOT/frontend/node_modules/.bin/vite" ]; then
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund)
fi

pkill -f "uvicorn app.main" 2>/dev/null || true
pkill -f "node.*vite" 2>/dev/null || true
sleep 1

(cd "$ROOT/backend" && V3D_DATA_DIR=${V3D_DATA_DIR:-/tmp/v3d-web} V3D_ENGINE_MODE=${V3D_ENGINE_MODE:-mock} \
  nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8765 > /tmp/v3d-backend.log 2>&1 &)
(cd "$ROOT/frontend" && nohup npm run dev > /tmp/v3d-frontend.log 2>&1 &)

for _ in $(seq 1 20); do
  code=$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:5173/ || true)
  [ "$code" = "200" ] && break
  sleep 0.5
done

echo "frontend: $(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:5173/)"
echo "backend:  $(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/health)"
