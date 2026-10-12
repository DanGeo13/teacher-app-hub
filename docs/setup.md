# Setup

## Quick start in GitHub Codespaces

For the browser-first launch, create a Codespace from this repository and set the administrator password as a Codespaces secret named `HUB_ADMIN_PASSWORD` before launching it. Use a unique password of at least 16 characters; do not add it to a repository file or variable. The backend requires this password before it starts, so the browser cannot safely create it on first launch.

After the Codespace is created, `postStartCommand` builds the frontend if needed and starts the Hub backend. Port 9120 stays private and opens in the browser. Sign in with the password stored in the Codespaces secret. The app and API are served together from that port.

To configure the secret, open the repository's Codespaces settings, add `HUB_ADMIN_PASSWORD`, then create or restart the Codespace. The Codespace must have access to the secret. GitHub's secret availability can depend on repository and organisation policy.

The Codespace can stop when idle. Reopen or restart it to use the Hub; the app is not continuously hosted when the Codespace is stopped. Its registry and session data live in `/workspaces/.hermes-hub`, outside the Git checkout. Keep port 9120 private. This setup does not install or enable Hermes/Ollama and does not configure a public deployment.

## Development sandbox

Requirements observed here: Python 3.11, Node 22 and npm 10. Runtime data must be outside the repository.

```bash
cd /home/user/teacher-app-hub
bash scripts/bootstrap-dev.sh

export HUB_ADMIN_PASSWORD='choose-a-unique-password-of-at-least-16-characters'
export HUB_DATA_DIR="$HOME/.hermes-hub-dev"
export HUB_COOKIE_SECURE=false                 # loopback HTTP only
export HUB_ALLOWED_ORIGINS='http://127.0.0.1:9120' # exact browser origin
bash scripts/run-local.sh
```

Open `http://127.0.0.1:9120`. `run-local.sh` binds to loopback, explicitly disables Uvicorn proxy-header processing and does not expose a public port.

The HTTP cookie exception is only for loopback development. Do not use `HUB_COOKIE_SECURE=false` on a remote or shared host. `HUB_ALLOWED_ORIGINS` is required and is an exact allow-list; forwarded headers are not trusted.

## Tests

These commands are local validation only. They do not start Hermes or Ollama, write to GitHub, deploy Apps Script, rebuild a Codespace or enable an external-write broker.

```bash
Apps/system/hermes-hub/backend/.venv/bin/pytest
npm --prefix Apps/system/hermes-hub/frontend test
npm --prefix Apps/system/hermes-hub/frontend run typecheck
npm --prefix Apps/system/hermes-hub/frontend run build
python3 scripts/scan-secrets.py
npm --prefix Apps/system/hermes-hub/frontend audit --audit-level=high
Apps/system/hermes-hub/backend/.venv/bin/python -m pip check
git diff --check
```

Browser validation uses the pinned Playwright package and an isolated synthetic backend. It creates only `/tmp/hermes-hub-e2e`, uses a synthetic password and never touches the configured personal data directory:

```bash
cd Apps/system/hermes-hub/frontend
npx playwright install chromium
npm run build
npm run test:e2e
```

The suite has four scenarios: login/logout invalidation, visible failed logout, authenticated API 401 session return, and registry add/reorder/edit/archive/restore persistence. CI installs Chromium with system dependencies and retains only failure PNGs for seven days; traces and video are disabled.

## Non-destructive Codespaces validation

Do not rebuild, migrate or alter an existing Codespace automatically for validation. Run these checks in the current checkout only:

```bash
git status --short --branch
git diff --check
bash scripts/bootstrap-dev.sh
Apps/system/hermes-hub/backend/.venv/bin/pytest
npm --prefix Apps/system/hermes-hub/frontend test
npm --prefix Apps/system/hermes-hub/frontend run typecheck
npm --prefix Apps/system/hermes-hub/frontend run build
python3 scripts/scan-secrets.py
npm --prefix Apps/system/hermes-hub/frontend audit --audit-level=high
Apps/system/hermes-hub/backend/.venv/bin/python -m pip check
```

Stop at review if any command fails. Do not run `git clean`, delete the data directory, install Hermes/Ollama, enable a broker or change port visibility as part of this validation.

## Eventual Codespace

The devcontainer sets `HUB_DATA_DIR=/workspaces/.hermes-hub`, forwards 9120 and 9119 privately and does not forward 11434. It does not set or migrate `HERMES_HOME`.

1. Rebuild/open the repository in a Codespace only as an explicit operator action, never as part of validation.
2. Store `HUB_ADMIN_PASSWORD` as a Codespaces secret, not a repository variable/file.
3. Set the exact private forwarded URL as `HUB_ALLOWED_ORIGINS`, for example `https://<codespace>-9120.app.github.dev`.
4. Keep `HUB_COOKIE_SECURE=true`.
5. Build the frontend and start Uvicorn without proxy-header processing:

```bash
npm --prefix Apps/system/hermes-hub/frontend run build
export HUB_FRONTEND_DIST="$PWD/Apps/system/hermes-hub/frontend/dist"
Apps/system/hermes-hub/backend/.venv/bin/python -m uvicorn \
  hermes_hub_backend.main:create_app_from_env --factory \
  --host 0.0.0.0 --port 9120 --no-proxy-headers
```

Binding `0.0.0.0` is needed inside a Codespace container, but the forwarded port must remain private. The Hub accepts only the configured exact Origin and ignores `X-Forwarded-Host`. This configuration does not revive a stopped Codespace. Hermes/Ollama installation and migration are separate, unapproved actions.

## Registry export

`GET /api/registry/export` returns the authenticated runtime registry. Review the result, compare it with `config/apps.registry.json`, validate it and request explicit commit approval. A click in the Hub never commits to Git.
