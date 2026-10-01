#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="$repo_root/Apps/system/hermes-hub/backend"
frontend="$repo_root/Apps/system/hermes-hub/frontend"

: "${HUB_DATA_DIR:?Set HUB_DATA_DIR to a writable location outside the repository}"
: "${HUB_ADMIN_PASSWORD:?Set HUB_ADMIN_PASSWORD to a unique value of at least 16 characters}"

export HUB_COOKIE_SECURE="${HUB_COOKIE_SECURE:-false}"
export HUB_ALLOWED_ORIGINS="${HUB_ALLOWED_ORIGINS:-http://127.0.0.1:9120}"
export HUB_FRONTEND_DIST="${HUB_FRONTEND_DIST:-$frontend/dist}"

if ! [ -f "$frontend/dist/index.html" ]; then
  npm --prefix "$frontend" run build
fi

exec "$backend/.venv/bin/python" -m uvicorn \
  hermes_hub_backend.main:create_app_from_env --factory \
  --host 127.0.0.1 --port "${HUB_PORT:-9120}" \
  --no-proxy-headers
