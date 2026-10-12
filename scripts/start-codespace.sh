#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="$repo_root/Apps/system/hermes-hub/backend"
frontend="$repo_root/Apps/system/hermes-hub/frontend"

: "${HUB_ADMIN_PASSWORD:?Set HUB_ADMIN_PASSWORD as a Codespaces secret before starting the Hub}"
: "${CODESPACE_NAME:?This startup script is intended for GitHub Codespaces}"

# Codespaces sends traffic to the forwarded port over HTTPS. Allow this exact
# Codespace/port origin and never make the forwarded port public here.
export HUB_DATA_DIR="${HUB_DATA_DIR:-/workspaces/.hermes-hub}"
export HUB_COOKIE_SECURE=true
export HUB_ALLOWED_ORIGINS="https://${CODESPACE_NAME}-9120.app.github.dev"
export HUB_FRONTEND_DIST="$frontend/dist"

if ! [ -f "$frontend/dist/index.html" ]; then
  npm --prefix "$frontend" run build
fi

exec "$backend/.venv/bin/python" -m uvicorn \
  hermes_hub_backend.main:create_app_from_env --factory \
  --host 0.0.0.0 --port 9120 --no-proxy-headers
