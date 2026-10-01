import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  outputDir: 'test-results',
  use: {
    baseURL: 'http://127.0.0.1:9121',
    // The app's service worker claims clients on activation, and App.tsx reloads
    // the page on `controllerchange`. Each test opens a fresh context, so that
    // forced reload fires mid-test (~150-500ms in) and races with sign-in: when
    // it lands between filling the password and the login response, the field is
    // wiped and the session is never established. Blocking service-worker
    // registration removes that non-deterministic reload; the worker's caching
    // policy stays covered by src/service-worker-policy.test.ts and
    // tests/backend/test_service_worker_policy.py.
    serviceWorkers: 'block',
    screenshot: 'only-on-failure',
    trace: 'off',
    video: 'off',
  },
  webServer: {
    command: "rm -rf /tmp/hermes-hub-e2e && mkdir -p /tmp/hermes-hub-e2e && HUB_ADMIN_PASSWORD='E2E-only-password-1234' HUB_DATA_DIR=/tmp/hermes-hub-e2e HUB_COOKIE_SECURE=false HUB_ALLOWED_ORIGINS=http://127.0.0.1:9121 HUB_FRONTEND_DIST=$PWD/dist ../backend/.venv/bin/python -m uvicorn hermes_hub_backend.main:create_app_from_env --factory --host 127.0.0.1 --port 9121 --no-proxy-headers",
    url: 'http://127.0.0.1:9121/api/health',
    reuseExistingServer: false,
    timeout: 20_000,
  },
})
