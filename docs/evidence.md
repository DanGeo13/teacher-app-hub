# Evidence matrix

**Status date:** 2 October 2026 (Australia/Sydney)
**Rule:** each executed setup/configuration row below has at least five distinct references. A reference supports only the claim stated in its row. Documentation support, local observation and proposed future work are kept separate.

Statuses: **VERIFIED** means the documented action was also exercised locally; **DOCUMENTED / NOT RUN** means the references are sufficient but the target integration is unavailable; **EVIDENCE-INCOMPLETE** means the action must not be executed without an explicit exception.

## Pinned upstream facts

GitHub release metadata and the tag itself were checked before configuration was written:

- Stable tag: `v2026.9.24`.
- Package version: `0.21.5`.
- Tag commit: `f97608f178d1ffeca59860195ab7da295f7c8e5f`.
- Pinned `pyproject.toml` requires Python `>=3.11,<3.14`.
- The custom Hub does not install this release; the pin is recorded only as the compatibility baseline.

Primary artefacts:

1. [Release v2026.9.24](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.24) — release/version/commit provenance.
2. [Pinned pyproject.toml](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/pyproject.toml) — package version, Python range and dependency metadata.
3. [Pinned installer source](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/scripts/install.sh) — supported install arguments and `HERMES_HOME`; not executed.
4. [Pinned dashboard guide](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/website/docs/user-guide/features/web-dashboard.md) — port 9119, authentication behaviour and `/api/status`.
5. [Pinned programmatic integration guide](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/website/docs/developer-guide/programmatic-integration.md) — ACP/TUI Gateway/API protocol boundaries.

There is a documentation inconsistency at this tag: the dashboard guide describes HTTP/PTY dependencies as optional extras while the pinned project metadata places several server/PTY packages in core dependencies. The installed command must therefore be probed rather than inferred.

## Executed foundation steps

### E1 — Isolated Python backend dependencies

| Field | Record |
|---|---|
| Exact action | `python3 -m venv Apps/system/hermes-hub/backend/.venv`; install `requirements.lock`; install the local package with `--no-deps -e` |
| Versions | Python 3.11.2; FastAPI 0.142.2; Pydantic 2.13.5; Uvicorn 0.54.0; pytest 9.1.1; HTTPX 0.28.1 |
| References | 1. [Python `venv`](https://docs.python.org/3/library/venv.html) — project isolation. 2. [PyPA pyproject guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/) — dependency metadata. 3. [FastAPI manual deployment](https://fastapi.tiangolo.com/deployment/manually/) — ASGI application/Uvicorn execution. 4. [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/) — validated request/response models. 5. [Uvicorn settings](https://www.uvicorn.org/settings/) — host, port and factory settings. 6. [PyPI JSON API](https://docs.pypi.org/api/json/) — package release metadata used to confirm pins. |
| Local verification | Package import, backend startup and pytest suite are recorded in `docs/acceptance-results.md`. |
| Limitations | The lock was generated on Debian x86-64/Python 3.11; other platforms must resolve compatible wheels without changing direct pins. |

### E2 — React/TypeScript frontend build

| Field | Record |
|---|---|
| Exact action | `npm ci` then `npm run build` under `Apps/system/hermes-hub/frontend` |
| Versions | React/React DOM 19.3.0; TypeScript 7.0.2; Vite 8.3.2; plugin-react 6.1.1; Vitest 5.0.3; Playwright 1.63.0 |
| References | 1. [React build-from-scratch guide](https://react.dev/learn/build-a-react-app-from-scratch) — React with a Vite build tool. 2. [Vite guide](https://vite.dev/guide/) — Node requirements, dev/build commands and `public` assets. 3. [TypeScript tsconfig reference](https://www.typescriptlang.org/tsconfig/) — compiler configuration. 4. [npm package-lock documentation](https://docs.npmjs.com/cli/configuring-npm/package-lock-json) — reproducible dependency tree. 5. [Vitest guide](https://vitest.dev/guide/) — Vite-native tests. 6. [Playwright test documentation](https://playwright.dev/docs/intro) — browser-level test runner. |
| Local verification | Type checking, production build and frontend tests are recorded in `docs/acceptance-results.md`. |
| Limitations | Browser binaries are development artefacts and are not committed. |

### E3 — SQLite WAL, migrations and consistent local snapshots

| Field | Record |
|---|---|
| Exact configuration | Hub database under `HUB_DATA_DIR`; `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`; numbered SQL migrations; Python `Connection.backup()` into a new file |
| Version | Python SQLite 3.40.1; schema version 2 |
| References | 1. [Python `sqlite3`](https://docs.python.org/3/library/sqlite3.html) — connection, transactions and `Connection.backup`. 2. [SQLite WAL](https://sqlite.org/wal.html) — WAL semantics, same-host constraint and auxiliary files. 3. [SQLite online backup API](https://sqlite.org/backup.html) — consistent live backup. 4. [SQLite foreign keys](https://sqlite.org/foreignkeys.html) — per-connection enforcement. 5. [SQLite transactions](https://sqlite.org/lang_transaction.html) — explicit `BEGIN IMMEDIATE` writer behaviour. 6. [SQLite atomic commit](https://sqlite.org/atomiccommit.html) — commit/recovery model. |
| Local verification | Migration, restart persistence, snapshot integrity and restore-to-new-file tests are recorded in `docs/acceptance-results.md`. |
| Limitations | Local snapshots share the workspace failure domain and are not deletion-safe external backups. Live in-place restore is disabled. |

### E4 — Server-side sessions, cookies and CSRF

| Field | Record |
|---|---|
| Exact configuration | CSPRNG session/CSRF tokens; only a SHA-256 session-token digest stored; `HttpOnly`, `SameSite=Strict`, `Path=/`; `Secure` by default; synchroniser token in `X-CSRF-Token`; Origin validation; API `no-store` headers |
| Version | Hub protocol 0.1; Python `secrets` from 3.11.2 |
| References | 1. [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) — server-side sessions and cookie attributes. 2. [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) — synchroniser tokens, custom headers and Origin checking. 3. [OWASP ASVS session requirements](https://github.com/OWASP/ASVS/blob/master/4.0/en/0x12-V3-Session-management.md) — entropy and cookie controls. 4. [Python `secrets`](https://docs.python.org/3/library/secrets.html) — cryptographically strong token generation and comparison. 5. [MDN Set-Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie) — browser semantics for Secure, HttpOnly and SameSite. 6. [FastAPI response cookies](https://fastapi.tiangolo.com/advanced/response-cookies/) — framework cookie API. |
| Local verification | Authentication, missing/wrong CSRF, disallowed Origin and private-cache-header tests are recorded in `docs/acceptance-results.md`. |
| Limitations | A single environment-provided administrator password is a bootstrap boundary, not multi-user identity. Loopback HTTP development explicitly sets `HUB_COOKIE_SECURE=false`; production/Codespaces HTTPS must keep it true. |

### E5 — Conservative PWA shell

| Field | Record |
|---|---|
| Exact configuration | Manifest with 192/512 icons; service worker pre-caches only offline shell/manifest/icons; same-origin static assets may be cached; every `/api/` request is network-only; explicit update prompt |
| Version | Cache `hermes-hub-shell-v1`; protocol 0.1 |
| References | 1. [MDN web app manifests](https://developer.mozilla.org/en-US/docs/Web/Manifest) — install metadata. 2. [MDN service workers](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API) — fetch interception/offline boundary. 3. [MDN Cache API](https://developer.mozilla.org/en-US/docs/Web/API/Cache) — request/response caching. 4. [Service-worker lifecycle](https://web.dev/service-worker-lifecycle/) — install/waiting/controller update states. 5. [web.dev PWA updates](https://web.dev/learn/pwa/update) — user-visible update handling and stale-cache removal. 6. [MDN PWA service-worker tutorial](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Tutorials/CycleTracker/Service_workers) — versioned shell cache and offline page. |
| Local verification | Static policy tests and the production frontend build are recorded in `docs/acceptance-results.md`. |
| Limitations | Offline mode provides the shell and an honest disconnected notice, not registry data, Hermes, Ollama or agent execution. |

### E6 — Codespaces data path and private ports (configuration only)

| Field | Record |
|---|---|
| Exact configuration | `.devcontainer/devcontainer.json` sets `HUB_DATA_DIR=/workspaces/.hermes-hub`; privately forwards 9120 and official dashboard 9119; does not forward Ollama; does not set or migrate `HERMES_HOME` |
| Version | Dev Container JSON; Python 3.11 image; Node feature 22 |
| References | 1. [Codespaces deep dive](https://docs.github.com/en/codespaces/about-codespaces/deep-dive) — `/workspaces` persistence across rebuilds. 2. [Codespaces port forwarding](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace) — private-default forwarding and visibility. 3. [Codespaces lifecycle](https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle) — idle stop and deletion boundaries. 4. [Development Containers specification](https://containers.dev/implementors/json_reference/) — lifecycle, environment and port attributes. 5. [Codespaces repository configuration](https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/introduction-to-dev-containers) — repository devcontainer behaviour. 6. [Codespaces secrets](https://docs.github.com/en/codespaces/managing-your-codespaces/managing-secrets-for-your-codespaces) — secret injection rather than committed credentials. |
| Local verification | **BLOCKED:** this host is not a Codespace and has no `/workspaces`. JSON is reviewed but persistence/forwarding is not claimed as tested here. |
| Limitations | A separate Codespace may already have Hermes state elsewhere. No migration is automatic. The external Codespaces URL must be placed in `HUB_ALLOWED_ORIGINS`. |

### E7 — GitHub Actions validation workflow

| Field | Record |
|---|---|
| Exact configuration | Read-only `contents` permission; checkout; Python 3.11; Node 22; install/test/build/secret scan; no Hermes/Ollama/deploy job |
| Versions | `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4` |
| References | 1. [Workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions) — triggers/jobs/steps. 2. [Workflow permissions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication) — least-privilege `GITHUB_TOKEN`. 3. [checkout action](https://github.com/actions/checkout) — source checkout. 4. [setup-python action](https://github.com/actions/setup-python) — Python toolchain. 5. [setup-node action](https://github.com/actions/setup-node) — Node/npm and cache. 6. [Secure use reference](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions) — untrusted input and credential guidance. |
| Local verification | Local commands match the steps. **NOT RUN on GitHub:** no push is authorised. |
| Limitations | This is CI only, never an always-on runtime or deployment path. |

## Runtime adapter steps

### E8 — Hermes availability probe

| Field | Record |
|---|---|
| Exact action | Server-side `hermes --version` when the executable exists, then unauthenticated read-only `GET {HERMES_DASHBOARD_URL}/api/status`; 401/403 becomes `authentication_required`, never an auth bypass |
| Compatibility baseline | Hermes 0.21.5 / `v2026.9.24`; actual installed version unknown |
| References | 1. [Pinned release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.24) — compatibility baseline. 2. [Pinned CLI reference](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/website/docs/reference/cli-commands.md) — CLI and dashboard commands. 3. [Pinned dashboard guide](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/website/docs/user-guide/features/web-dashboard.md) — `/api/status`, port and auth. 4. [Pinned programmatic integration](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/website/docs/developer-guide/programmatic-integration.md) — supported integration protocols. 5. [Pinned environment reference](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/website/docs/reference/environment-variables.md) — dashboard auth/public URL configuration. 6. [Hermes security guide](https://hermes-agent.nousresearch.com/docs/user-guide/security) — approval/security boundaries. |
| Local verification | Probe returns structured `unavailable`; capability fields remain `not_run`. |
| Gaps | **BLOCKED:** no authentic session, streaming event, approval or tool call can be tested on this host. Dashboard reachability will not be treated as capability proof. |

### E9 — Ollama/Qwen availability probe

| Field | Record |
|---|---|
| Exact action | Server-side read-only `GET {OLLAMA_BASE_URL}/api/version`; no browser request, model listing, generation or model download |
| Version | Ollama and model version unknown; desired `qwen3.5:4b` not installed here |
| References | 1. [Ollama API introduction](https://docs.ollama.com/api/introduction) — local base URL and endpoint catalogue. 2. [Ollama OpenAPI specification](https://docs.ollama.com/openapi.yaml) — `GET /api/version` response. 3. [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) — separate `/v1` model API and feature claims. 4. [Structured outputs](https://docs.ollama.com/capabilities/structured-outputs) — schema-output mechanism requiring an actual request. 5. [Official qwen3.5 model entry](https://ollama.com/library/qwen3.5) — model tags/declared inputs, not local proof. 6. [Ollama Hermes integration](https://docs.ollama.com/integrations/hermes) — documented Hermes/Ollama topology. |
| Local verification | Probe returns structured `unavailable`; text, vision, tools and structured output remain `not_run`. |
| Gaps | **BLOCKED:** no runtime/model is installed and downloads are not authorised. Reachability alone cannot promote a capability to verified. |

## Not executed integration steps

### E10 — Existing Hermes skills/workflow ingestion

Exact future action: use the installed version's `hermes skills list`, `inspect` and `audit` interfaces; record source, revision/hash, licence and permissions before adoption. References: [skills system](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [CLI skills commands](https://hermes-agent.nousresearch.com/docs/reference/cli-commands), [security guide](https://hermes-agent.nousresearch.com/docs/user-guide/security), [optional skills catalogue](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/optional-skills-catalog.md), and a [concrete bundled skill with provenance metadata](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent.md).

Local verification: **BLOCKED — no installed Hermes home or skills.** No skill was installed or executed.

### E11 — End-to-end multimodal and tool execution

Exact future verification must include one real text response, one real image input, one model-emitted tool call, tool-result continuation and one schema-validated response, with request IDs and resource observations. Supporting references: [Hermes programmatic integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration), [Hermes CLI image/query options](https://hermes-agent.nousresearch.com/docs/reference/cli-commands), [Hermes vision guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/vision), [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility), [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs), and the [Ollama OpenAPI](https://docs.ollama.com/openapi.yaml).

Local verification: **BLOCKED — no Hermes/Ollama/model.** No canned or mock success is substituted.

### E12 — GAS/clasp integration

Candidate references have been identified, but no script, deployment, Google identity, current clasp binary or deployed application is available. The exact deployment action and target therefore remain **EVIDENCE-INCOMPLETE**. No Google authentication, Apps Script write or iframe claim was attempted.

## E13 — Hermes Hub hardening and browser validation

| Field | Record |
|---|---|
| Exact action | Enforce exact configured Origins without forwarded-header trust; remove the short-password bypass; reject malformed/control-character URLs; harden 0600 local snapshot creation and bounded cleanup; handle frontend logout failures and 401 session expiry; keep service-worker caching to the public shell; add concurrency, privacy, CI-policy and browser regression coverage |
| Browser suite | Four scenarios in `Apps/system/hermes-hub/frontend/e2e/registry.spec.ts`: login/logout invalidation, visible failed logout, authenticated API 401 return to sign-in, and registry add/reorder/edit/archive/restore/reload persistence |
| CI boundary | Chromium is installed with pinned Playwright system dependencies. CI runs only synthetic backend/browser validation, uploads failure PNGs for seven days, disables trace/video, and has read-only contents permission. It never runs Hermes/Ollama, enables external-write brokers, implements approval expiry or migration checksums, or changes Codespaces state. |
| Local verification | Backend 26 passed; frontend tests 3 passed; TypeScript and Vite build passed; secret scan, npm audit and pip check passed; Playwright discovery lists exactly four tests. Executable browser run is pending the CI Chromium job because this host could not download Chromium. |
| Codespaces boundary | `docs/setup.md` contains validation-only commands and explicitly prohibits automatic rebuild, migration, cleanup, broker enablement or port-visibility changes. |

## E14 — Minimal authentic Hermes session integration

| Field | Record |
|---|---|
| Exact action | Drive the installed Hermes executable over the documented ACP v1 stdio protocol: `initialize`, `session/new`, `session/prompt`, `session/resume`; stream one real user message per request as SSE; persist session references (id, timestamps, agent/model/provider metadata, stop reason/error) with no message content; reject tool-permission requests fail-closed |
| Real-instance verification | `hermes-agent[acp]==0.19.0` installed from PyPI; `hermes acp --check` OK. Integration tests (`tests/backend/test_hermes_integration.py`) against the real binary: new-session stream and follow-up resume turn **both passed** with a synthetic local OpenAI-compatible endpoint standing in for Ollama (no external service). Without a provider configured, the same tests verified the honest refusal state (failed reference + agent remediation) before skipping BLOCKED. |
| Without the executable | Both integration tests self-skip BLOCKED with the exact remedy (`HUB_TEST_HERMES_EXECUTABLE`); no session is simulated. Adapter and service behaviour is covered by the synthetic-agent unit/HTTP suite. |
| Local verification | Backend 50 passed (incl. both real-Hermes integration tests) / 48 passed + 2 BLOCKED skips without Hermes; frontend typecheck, vitest (4) and Vite build passed. |
| Boundary | No orchestration, no Hermes-internal state access, no external writes, no UI beyond a read-only session-reference card on Maintenance. |
