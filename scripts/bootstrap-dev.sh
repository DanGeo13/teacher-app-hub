#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend="$repo_root/Apps/system/hermes-hub/backend"
frontend="$repo_root/Apps/system/hermes-hub/frontend"

python3 -m venv "$backend/.venv"
"$backend/.venv/bin/python" -m pip install --disable-pip-version-check -r "$backend/requirements.lock"
"$backend/.venv/bin/python" -m pip install --disable-pip-version-check --no-deps -e "$backend"
npm --prefix "$frontend" ci

printf 'Hermes Hub development dependencies are ready. No services were started.\n'
