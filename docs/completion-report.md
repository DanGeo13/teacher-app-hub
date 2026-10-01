# Milestone 0.1 completion report

**Date:** 2 October 2026 (Australia/Sydney)
**Branch:** `arena/01a0f7c3-teacher-app-hub`
**Base:** `c9c07a4f471ef27d3516a5b06f18e63c6f669b55`
**Repository state at milestone hand-off:** implementation was intentionally uncommitted. The user subsequently authorised the scoped commit, branch push and pull request; Git history is authoritative for those later actions.

## Requirement and delivered behaviour

The accepted milestone was a durable, honest disconnected vertical slice rather than a simulated autonomous platform. An authenticated user can use the responsive same-origin Hub to navigate Dashboard, Teacher Hub, Lifestyle Hub and Maintenance; create, edit, categorise, reorder, archive and reopen application records; restart the backend without losing registry data; inspect honest Hermes/Ollama reachability and separate unverified capability states; and create a consistent local SQLite snapshot.

No model response, Hermes session, agent hand-off, deployment or external backup is simulated. Every exposed restricted action returns unavailable.

## File groups

- `Apps/system/hermes-hub/backend/` — FastAPI/Pydantic API, authentication/CSRF, SQLite migrations, registry/audit/approval/backup services and runtime probes.
- `Apps/system/hermes-hub/frontend/` — React/TypeScript responsive PWA, registry forms/cards, runtime and maintenance views, privacy policy tests and generated local icons.
- `config/`, `packages/shared-schemas/` — versioned empty registries, theme tokens and JSON schemas for apps, approvals, agents, workflows and hand-offs.
- `packages/hub-sdk/`, `packages/gas-adapter/`, `agents/` — explicit schema/documentation boundaries; integrations are not falsely enabled.
- `tests/backend/`, `tests/e2e/` — isolated synthetic backend coverage and a real-browser add/reload test ready to run when Chromium is available.
- `.devcontainer/`, `.github/workflows/`, `scripts/` — private-port Codespaces configuration, validation-only CI, reproducible bootstrap/startup and secret scanning.
- `docs/` — architecture, setup, security, operations, evidence, backup/restore, model-capability, GAS, acceptance and limitation records.

No live database, backup, dependency directory, `.env`, credential, private chat, student data, model weight or production artefact is a Git candidate.

## Stage history

1. **Read-only discovery:** verified branch/base, initially clean one-file checkout, host resources, installed tools and absent Hermes/Ollama/Codespaces state.
2. **Evidence and design:** checked immutable Hermes 0.21.5 release artefacts and official Hermes, Ollama, Codespaces, SQLite, OWASP, React/Vite/FastAPI and PWA references. Recorded the step mapping in `docs/evidence.md`.
3. **Foundation:** added schemas, migrations, backend security/persistence boundaries and honest fixed-target runtime adapters.
4. **Vertical slice:** added the responsive frontend, PWA lifecycle, registry workflows, maintenance/snapshot view and Australian English copy.
5. **Verification:** installed pinned project-local dependencies, generated lockfiles, ran automated/API/build/secret/startup checks, found and repaired schema, migration/auth, approval-transaction, edit-payload and TypeScript issues.
6. **Browser attempt:** Playwright's Chromium download failed after five automatic CDN attempts with `ECONNRESET`; no installed browser executable was available. This remains a precise blocker, not a mocked pass.
7. **Review:** tightened database permissions, service-worker allowlisting, production source-map policy, CSP and schema/config parsing. No commit, push, PR, deployment, model download, Google login or public port occurred.

## Verification summary

- Backend: **18 passed**; one upstream Starlette TestClient deprecation warning.
- Frontend: **3 passed**; TypeScript check passed.
- Production build: passed with Vite 8.3.2; no source maps.
- Startup: Uvicorn started on loopback only, served the UI/security headers, passed health/integrity, login/CSRF and authenticated registry checks, then stopped.
- Setup script: passed and remained side-effect limited to project-local dependencies.
- Shell/Python compilation/11 JSON documents/secret scan/`git diff --check`: passed.
- Browser add/reload test: **BLOCKED by browser binary download**, test retained in `tests/e2e/registry.spec.ts`.

Detailed commands and output are in `docs/acceptance-results.md`.

## Evidence gaps and blockers

- The actual reported Codespace/Hermes installation is not this sandbox; existing Hermes functionality cannot be exercised or migrated here.
- Hermes session, stream, tool and approval behaviour is not verified.
- Ollama/Qwen text, image, tool and structured-output behaviour is not verified; no model was downloaded.
- Codespaces `/workspaces` persistence, private forwarding and secure-cookie behaviour are documented/configured but untested here.
- No GAS project, deployment, Google identity or clasp installation is available.
- No independent encrypted external backup or fresh-workspace restore is implemented.
- Real-browser and mobile/PWA interaction testing is blocked by the unavailable browser binary.

## Security and persistence limitations

- Authentication is a single environment-provided administrator password, not multi-user identity/MFA.
- CSRF, secure-cookie, CSP and Origin controls depend on correct HTTPS/origin configuration. `HUB_COOKIE_SECURE=false` is loopback-only.
- SQLite data is durable only for the configured host path. Local snapshots share its failure domain.
- Live restore, Git/clasp/deployment and external writes are not implemented and fail closed.
- The secret scanner detects selected high-confidence patterns; it is not a DLP proof.
- A stopped Codespace cannot provide backend, Hermes or local model availability. The PWA provides only an honest shell/offline notice.

## Diff and artefact

- Patch against base: `96 files changed, 5,797 insertions(+), 1 deletion(-)`.
- Local binary-safe patch: `hermes-hub-milestone.patch` (ignored by Git, not committed).
- Patch verification: reverse-apply check is run against the current working tree after generation.

## Startup commands

```bash
bash scripts/bootstrap-dev.sh
npm --prefix Apps/system/hermes-hub/frontend run build
export HUB_ADMIN_PASSWORD='choose-a-unique-password-of-at-least-16-characters'
export HUB_DATA_DIR="$HOME/.hermes-hub-dev"
export HUB_COOKIE_SECURE=false
export HUB_ALLOWED_ORIGINS='http://127.0.0.1:9120'
bash scripts/run-local.sh
```

This opens only `127.0.0.1:9120`. Use the separate private Codespaces instructions in `docs/setup.md` for a container.

## Exact approval package — subsequently authorised

Review first:

```bash
git status --short
git diff -- README.md
git apply --stat hermes-hub-milestone.patch
python3 scripts/scan-secrets.py
Apps/system/hermes-hub/backend/.venv/bin/pytest -q
npm --prefix Apps/system/hermes-hub/frontend test
npm --prefix Apps/system/hermes-hub/frontend run build
```

After the milestone review, the user explicitly authorised the following local commit steps:

```bash
git add --all
git diff --cached --check
git diff --cached --stat
git commit -m "feat: add durable Hermes Hub foundation"
```

The same request authorised this branch-only GitHub push:

```bash
git push origin arena/01a0f7c3-teacher-app-hub
```

The authorised follow-up is a pull request from this branch to `main`. No merge is authorised.

## Rollback

Before commit, preserve the patch elsewhere if wanted, then use `git restore README.md` and remove only the newly listed milestone paths. Do not use a broad destructive clean in a workspace that may contain unrelated work. After a future approved commit, prefer `git revert <approved-commit>` so history remains auditable. Runtime rollback is to stop the process and retain `HUB_DATA_DIR`; never delete it as part of code rollback.
