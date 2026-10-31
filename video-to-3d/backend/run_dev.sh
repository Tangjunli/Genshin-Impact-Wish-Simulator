#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export V3D_ENGINE_MODE="${V3D_ENGINE_MODE:-auto}"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${V3D_PORT:-8765}" --reload
