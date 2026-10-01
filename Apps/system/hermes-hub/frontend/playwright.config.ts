import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '../../../../tests/e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:9121',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: "rm -rf /tmp/hermes-hub-e2e && mkdir -p /tmp/hermes-hub-e2e && HUB_ADMIN_PASSWORD='E2E-only-password-1234' HUB_DATA_DIR=/tmp/hermes-hub-e2e HUB_COOKIE_SECURE=false HUB_ALLOWED_ORIGINS=http://127.0.0.1:9121 HUB_FRONTEND_DIST=$PWD/dist ../backend/.venv/bin/python -m uvicorn hermes_hub_backend.main:create_app_from_env --factory --host 127.0.0.1 --port 9121",
    url: 'http://127.0.0.1:9121/api/health',
    reuseExistingServer: false,
    timeout: 20_000,
  },
})
