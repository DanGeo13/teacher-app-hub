# Acceptance results

**Milestone:** 0.1 — durable, honest disconnected vertical slice
**Data:** synthetic only.
**Status date:** 2 October 2026 (Australia/Sydney).

## Milestone verification

| Verification | Status | Command/evidence |
|---|---|---|
| Registry create/edit/reorder/archive and hub categories | PASS | `pytest`: `test_registry_create_edit_reorder_archive_and_categories` |
| Persistence after backend restart | PASS | `test_registry_persists_after_backend_restart` reopens the same temporary SQLite database and session |
| Unsafe URL/path rejection | PASS | `test_invalid_and_unsafe_urls_are_rejected` |
| Revision conflicts | PASS | `test_revision_conflict` returns HTTP 409 |
| Authentication, cookie and CSRF enforcement | PASS | missing Origin/session/CSRF, bad CSRF and foreign Origin rejected |
| Approval invalidation and replay rejection | PASS | service and API tests; changed binding remains invalidated transactionally |
| Restricted actions fail closed | PASS | HTTP 503 `BROKER_NOT_IMPLEMENTED` |
| Missing Hermes/Ollama explicit states | PASS | both `unavailable`; capabilities remain `not_run` |
| Private API/service-worker cache exclusions | PASS | backend headers plus Python/Vitest source-policy tests |
| Consistent snapshot and restore | PASS | online snapshot, integrity check and restore into new database |
| Frontend build/backend import-start | PASS | Vite build plus Uvicorn loopback smoke test |
| Secret scan | PASS | configured high-confidence repository and generated-bundle patterns absent |
| Playwright discovery and browser scenarios | PASS / EXECUTION PENDING CI | `npm run test:e2e -- --list` discovers exactly four scenarios from the frontend package; local Chromium download is blocked by host network policy, so executable browser results remain a CI review gate |

## Exact local results

```text
Apps/system/hermes-hub/backend/.venv/bin/pytest -q
18 passed, 1 Starlette TestClient deprecation warning in 1.59s

npm test
1 file passed; 3 tests passed

npm run typecheck
passed

npm run test:e2e -- --list
4 scenarios discovered; executable browser run pending CI because the local Chromium download was blocked

npm run build
Vite 8.3.2; 26 modules; build passed in 197ms
JS 241.67 kB (74.73 kB gzip); CSS 14.05 kB (4.05 kB gzip); no source maps

python3 scripts/scan-secrets.py
passed

npm audit --audit-level=high
0 vulnerabilities

python -m pip check
No broken requirements found

git diff --check
passed

bash scripts/bootstrap-dev.sh
passed; no service started
```

The loopback startup smoke test started Uvicorn on `127.0.0.1:9122`, served the frontend and CSP/frame headers, returned schema version 2 with database integrity `ok`, issued a login/CSRF pair and returned an authenticated empty registry. The process was then stopped. No public port was opened.

The single pytest warning is from Starlette 1.7.0 deprecating its HTTPX-backed `TestClient` in favour of a future `httpx2` package. It does not indicate a failed application test. Replacing pinned framework dependencies solely to suppress it was not justified.

## Original acceptance suite

1. Browser-only access — **PARTIAL:** same-origin built UI/API served successfully over HTTP; automated real-browser navigation is blocked by the unavailable browser binary.
2. Existing Hermes functionality preserved — **BLOCKED:** separate host unavailable; no Hermes files, installation or process were changed here.
3. Actual runtime sessions — **BLOCKED:** no Hermes runtime.
4. Three-agent automatic hand-off — **NOT IMPLEMENTED** in milestone 0.1.
5. Refresh preserves task/conversation references — **NOT IMPLEMENTED:** tasks/conversations are outside this slice.
6. Restart preserves expected state — **PASS** for application registry and server session in isolated state.
7. Fresh workspace external restore — **BLOCKED:** no independent external backup destination.
8. Interrupted workflow recovery — **NOT IMPLEMENTED**.
9. Teacher/Lifestyle registries editable/persistent — **PASS** at authenticated API/service level; browser path remains blocked.
10. Legacy URL added without Hub source changes — **PASS** at registry API/service level; no real GAS app was available.
11–22. GAS, agent development and deployment scenarios — **BLOCKED/NOT IMPLEMENTED**; integrations are unavailable and external writes remain disabled.
23. Stopped backend honest state — **PARTIAL:** offline page, shell-only cache and explicit disconnected copy pass source policy/build checks; real-browser offline transition is blocked.
24. Mobile/PWA behaviour — **PARTIAL:** responsive layouts, manifest/icons and update lifecycle build successfully; device/browser verification is blocked.
25. No secrets in source/generated bundle — **PASS** for configured scanner patterns; this is not a DLP guarantee.
26. Logs/backups do not leak student data — **PASS within synthetic scope**; tests use no real student data and audit details are bounded metadata.
27. Saturation/resource limits — **BLOCKED:** no model runtime and no model download authorised.
28. No unapproved paid endpoint/public port — **PASS:** none configured or started.

Screenshots are not used as proof of backend behaviour.
