# Setup

## Development sandbox

Requirements observed here: Python 3.11, Node 22 and npm 10. Runtime data must be outside the repository.

```bash
cd /home/user/teacher-app-hub
bash scripts/bootstrap-dev.sh
npm --prefix Apps/system/hermes-hub/frontend run build

export HUB_ADMIN_PASSWORD='choose-a-unique-password-of-at-least-16-characters'
export HUB_DATA_DIR="$HOME/.hermes-hub-dev"
export HUB_COOKIE_SECURE=false                 # loopback HTTP only
export HUB_ALLOWED_ORIGINS='http://127.0.0.1:9120'
bash scripts/run-local.sh
```

Open `http://127.0.0.1:9120`. `run-local.sh` binds to loopback and does not expose a public port.

The HTTP cookie exception is only for loopback development. Do not use `HUB_COOKIE_SECURE=false` on a remote or shared host.

## Tests

```bash
Apps/system/hermes-hub/backend/.venv/bin/pytest
npm --prefix Apps/system/hermes-hub/frontend test
npm --prefix Apps/system/hermes-hub/frontend run build
python3 scripts/scan-secrets.py
```

Browser test after installing the project-local Playwright Chromium binary:

```bash
cd Apps/system/hermes-hub/frontend
npx playwright install chromium
npm run build
npm run test:e2e
```

## Eventual Codespace

The devcontainer sets `HUB_DATA_DIR=/workspaces/.hermes-hub`, forwards 9120 and 9119 privately and does not forward 11434. It does not set or migrate `HERMES_HOME`.

1. Rebuild/open the repository in a Codespace.
2. Store `HUB_ADMIN_PASSWORD` as a Codespaces secret, not a repository variable/file.
3. Set the exact private forwarded URL as `HUB_ALLOWED_ORIGINS`, for example `https://<codespace>-9120.app.github.dev`.
4. Keep `HUB_COOKIE_SECURE=true`.
5. Build the frontend and start Uvicorn:

```bash
npm --prefix Apps/system/hermes-hub/frontend run build
export HUB_FRONTEND_DIST="$PWD/Apps/system/hermes-hub/frontend/dist"
Apps/system/hermes-hub/backend/.venv/bin/python -m uvicorn \
  hermes_hub_backend.main:create_app_from_env --factory \
  --host 0.0.0.0 --port 9120 --proxy-headers
```

Binding `0.0.0.0` is needed inside a Codespace container, but the forwarded port must remain private. This configuration does not revive a stopped Codespace. Hermes/Ollama installation and migration are separate, unapproved actions.

## Registry export

`GET /api/registry/export` returns the authenticated runtime registry. Review the result, compare it with `config/apps.registry.json`, validate it and request explicit commit approval. A click in the Hub never commits to Git.
