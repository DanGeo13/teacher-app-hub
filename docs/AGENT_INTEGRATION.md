# Hermes integration findings for Atlas

Atlas runs **on Hermes Agent**; the Hub only supplies Atlas's persona, skills,
tools (the `atlas-hub` MCP server), approval engine and schedules. This page
records how each Hermes extension point actually works, so nothing in
`agent/atlas/` relies on a guessed file name, config key or API.

## How this was verified

- Version checked: **hermes-agent 0.19.0**, the newest release on PyPI on
  11/10/2026 (`pip index` / PyPI JSON; release list ends `0.18.2, 0.19.0`).
- Method: the 0.19.0 wheel was downloaded and its source read directly, then
  installed into a throwaway virtual environment
  (`pip install "hermes-agent[mcp,cron,acp]==0.19.0"`, Python 3.13) so the
  repository tests could call Hermes' own functions. Source paths below are
  relative to the installed package.
- The official docs site (`hermes-agent.nousresearch.com`) could not be
  resolved from the build sandbox (DNS failure), so documentation claims were
  checked against the source and the public GitHub issue tracker instead.
- `tests/atlas/test_hermes_compat.py` re-runs the key checks against an
  installed Hermes whenever one is importable (set
  `ATLAS_TEST_HERMES_PYTHON` to its interpreter). Without Hermes those tests
  report **BLOCKED** (pytest skip with the reason); they never fake a pass.

## 1. Persona / system instructions

| Fact | Source |
|---|---|
| The identity slot of the system prompt is `$HERMES_HOME/SOUL.md`. If it is missing or empty, Hermes uses its built-in default identity. | `agent/prompt_builder.py::load_soul_md`, `agent/system_prompt.py` (stable tier) |
| SOUL.md is **scanned for prompt-injection patterns** (`tools/threat_patterns.py`, scope `context`). On any match the *whole file* is replaced by `[BLOCKED: SOUL.md contained potential prompt injection …]`. | `agent/prompt_builder.py::_scan_context_content` |
| Profiles: `hermes -p <name>` (or `--profile`) selects `~/.hermes/profiles/<name>/` as `HERMES_HOME`, with its own `config.yaml`, `SOUL.md`, skills, memories, cron store. | `hermes_cli/_parser.py`, `cron/jobs.py` header comment |
| Cron runs also load SOUL.md ("Cron jobs should always inherit the user's SOUL.md identity"), but not cwd context files unless the job sets `workdir`. | `cron/scheduler.py` (~line 3361) |
| Timezone: `HERMES_TIMEZONE` env var, then `timezone:` in `config.yaml`, then server local time. Cron expressions are evaluated with this timezone-aware clock. | `hermes_time.py`, `cron/jobs.py` (`croniter(expr, _hermes_now())`) |

**Consequence for Atlas.** The persona in `agent/atlas/persona/SOUL.md` is
installed as `~/.hermes/profiles/atlas/SOUL.md`. Because Hermes silently blocks
a SOUL.md that matches its injection scanner, a test runs Hermes' real
`scan_for_threats(..., scope="context")` on the persona. The persona text
passes the scanner verbatim (see the test output in
`docs/IMPLEMENTATION_STATUS.md`).

## 2. Skills (SKILL.md)

| Fact | Source |
|---|---|
| A skill is a directory containing `SKILL.md` with YAML frontmatter. Required: `name` (≤64 chars), `description` (≤1024 chars). Optional: `version`, `license`, `platforms` (`macos`/`linux`/`windows`; Termux also accepts `linux`, `termux`, `android`), `prerequisites`, `required_environment_variables`, `metadata.hermes.{tags,related_skills}`. | `tools/skills_tool.py` module docstring, `agent/skill_utils.py::parse_frontmatter` |
| Discovery: `$HERMES_HOME/skills/` first, then each directory in `skills.external_dirs` (config.yaml). | `agent/skill_utils.py::get_external_skills_dirs` |
| `external_dirs` are treated as **externally owned**: autonomous curator maintenance treats them as read-only (foreground tool calls can still edit them if a write-capable tool is enabled). | `agent/skill_utils.py::is_external_skill_path` |
| Skills can be disabled globally (`skills.disabled`) or per platform (`skills.platform_disabled.<platform>`). | `agent/skill_utils.py` (~line 370) |
| The agent loads skills through the `skills` toolset: `skills_list`, `skill_view` **and `skill_manage`** (create/edit skills). There is no per-tool disable: only whole toolsets can be turned off. | `toolsets.py` (`"skills"`), `agent.disabled_toolsets` handling |
| Cron jobs preload attached skills by calling `skill_view()` in Python, so this works even when the `skills` toolset is disabled for the cron platform. Loaded skill content is injection-scanned. | `cron/scheduler.py` (`_build_job_prompt`) |
| Telegram DM topics can bind one skill per topic (`platforms.telegram.extra.dm_topics[].topics[].skill`). | `plugins/platforms/telegram/adapter.py::_setup_dm_topics` |
| Inline shell in SKILL.md (`` !`cmd` ``) is off by default; leave it off. | `hermes_cli/config.py` (`skills` block) |

**Consequence for Atlas.** Remote platforms must not have `skill_manage`, so
the whole `skills` toolset is disabled there. Atlas still reaches its skills
three ways: cron jobs attach them by name; Telegram topics bind them; and the
`atlas-hub` MCP server serves reviewed skills read-only (`get_skill`). The
`atlas-hub` server refuses to serve a skill whose SHA-256 does not match
`agent/atlas/skills.lock.json`, and the startup policy check fails if any
installed skill differs from the lock. That makes an agent-modified skill
inert until Dan reviews it and updates the lock.

## 3. Scheduled (cron) jobs

| Fact | Source |
|---|---|
| Jobs are stored in `$HERMES_HOME/cron/jobs.json` (per profile); outputs go to `cron/output/<job_id>/`. They are runtime state, not a declarative file you commit. | `cron/jobs.py` header |
| Create with `hermes cron create <schedule> [prompt] --name --deliver --skill (repeatable) --script --no-agent --workdir`. Schedules: `30m` (one-shot), `every 30m`, 5-field cron (`0 8 * * 0`), or an ISO timestamp. | `hermes_cli/subcommands/cron.py`, `cron/jobs.py::parse_schedule` |
| The **CLI has no toolset flag**. A per-job `enabled_toolsets` list can only be set through the Python API (`cron.jobs.create_job(..., enabled_toolsets=[...])`) or the agent's `cronjob` tool. | `cron/jobs.py::create_job`, `tools/cronjob_tools.py` |
| Cron always disables `cronjob`, `messaging` and `clarify`, plus everything in `agent.disabled_toolsets`. Without a per-job list it uses `platform_toolsets.cron`. Enabled MCP servers are layered on unless the list contains `no_mcp` or already names servers. | `cron/scheduler.py::_resolve_cron_disabled_toolsets`, `_resolve_cron_enabled_toolsets`, `_merge_mcp_into_per_job_toolsets` |
| Cron agents run **non-interactively and auto-approve** tool-permission prompts. | `cron/scheduler.py` docstring (~line 150) |
| Each run is a fresh session. The prompt gets a preamble: deliver via the final response, and reply exactly `[SILENT]` to suppress delivery when nothing is new. | `cron/scheduler.py` (`SILENT_MARKER`, `cron_hint`) |
| Missed runs: a recurring job late by more than its grace window (half the period, clamped to 2 min–2 h) is **fast-forwarded, not caught up**. | `cron/jobs.py::_compute_grace_seconds` |
| Delivery targets: `origin`, `local`, `telegram`, or `platform:chat_id`; Telegram's home target is `TELEGRAM_HOME_CHANNEL`. | `cron/scheduler.py` (`_HOME_TARGET_ENV_VARS`) |

**Consequence for Atlas.** Schedules are declared in
`agent/atlas/schedules.yaml` and installed by
`agent/atlas/install/install_profile.py` through `create_job(...)`, so each
job has an explicit `enabled_toolsets`. Because Hermes skips long-missed runs,
**catch-up is implemented in the Hub** (`atlas/checks.py`): every scheduled
prompt starts with `get_check_status`, which reports the gap and asks for one
reconciliation run.

## 4. Tools, toolsets, MCP servers and per-platform restriction

| Fact | Source |
|---|---|
| Per-platform toolsets: `platform_toolsets.<platform>: [toolset, …]` in config.yaml. Platform keys include `cli`, `telegram`, `api_server`, `cron`, and others. An explicit list of configurable toolset names is authoritative. | `hermes_cli/tools_config.py::_get_platform_tools`, `hermes_cli/platforms.py` |
| Hermes **re-adds some non-configurable platform toolsets** implicitly, so reading config.yaml is not enough. The startup check therefore calls `_get_platform_tools` itself. | same function ("Recover non-configurable platform toolsets") |
| Global denylist: `agent.disabled_toolsets`, honoured by CLI, gateway and cron (cron adds it to its own denylist, so per-job lists cannot widen it). | `cli.py`, `gateway/run.py`, `cron/scheduler.py` |
| MCP servers: `mcp_servers.<name>` with `command`/`args`/`env` (stdio) or `url`/`headers` (HTTP). `tools.include` is a strict allowlist (it beats `exclude`); `tools.resources` / `tools.prompts` toggle the utility tools; `enabled: false` turns a server off. | `tools/mcp_tool.py` (docstring, `_register_server_tools`) |
| MCP tool names become `mcp__<server>__<tool>` (non-alphanumerics → `_`). A collision with a built-in tool is skipped. | `tools/mcp_tool.py::mcp_prefixed_tool_name` |
| stdio MCP servers get a **filtered environment**: only `PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TERM`, `SHELL`, `TMPDIR`, `XDG_*`, plus the server's `env:` block. | `tools/mcp_tool.py::_build_safe_env` |
| MCP servers may request LLM sampling unless `sampling.enabled: false`. | `tools/mcp_tool.py` |

**Consequence for Atlas.** Each mail account is its own `mcp_servers` entry
(`gmail_personal`, `gmail_work`) with an explicit `tools.include` list of
read-only tools, `resources`/`prompts` off and sampling off. A connector update
that adds `send_message` therefore cannot register it. Credentials are passed
by **file path** (`env: {..._FILE: /path}`) to a 0600 secret file outside the
repo, never inline.

## 5. Telegram topics and sessions

| Fact | Source |
|---|---|
| Session keys: a DM is keyed by chat id; a Telegram forum/DM topic (`message_thread_id`) gets its own session. Threads are shared by participants unless `thread_sessions_per_user`. | `gateway/session.py::build_session_key` |
| DM topics are declared under `platforms.telegram.extra.dm_topics`; Hermes creates missing topics and writes their `thread_id` back to config.yaml. | `plugins/platforms/telegram/adapter.py` |
| Access control: `TELEGRAM_ALLOWED_USERS` (env) allowlist plus pairing. | `gateway/authz_mixin.py`, `gateway/pairing.py` |
| Inline buttons exist only for Hermes' **own** flows: exec approval (`ea:`), slash confirm (`sc:`), clarify (`cl:`), model picker. The agent has no generic "attach buttons to my message" capability. | `plugins/platforms/telegram/adapter.py` (`callback_data=` sites) |

**Consequence for Atlas.** Approve/Deny buttons for Atlas actions are
**implemented in the Hub, not Hermes**: `atlas/telegram_approvals.py` drives a
*separate* approval bot (a second bot token, because two pollers on one token
conflict). Button presses are accepted only from Dan's configured Telegram user
id and go straight to the Hub approval engine.

## 6. Memory

| Fact | Source |
|---|---|
| Built-in memory is two bounded files per profile: `$HERMES_HOME/memories/MEMORY.md` (agent notes, ~2200 chars) and `USER.md` (user profile, ~1375 chars), `§`-delimited, injected as a frozen snapshot at session start. | `tools/memory_tool.py` |
| Memory entries are injection-scanned. `memory.write_approval: true` stages writes for review (`/memory pending`, `/memory approve <id>`, `/memory reject <id>`). | `hermes_cli/config.py` (`memory` block) |

**Consequence for Atlas.** Atlas uses Hermes memory for stable preferences,
with `memory.write_approval: true` so Dan inspects writes. Operational records
(bills, cars, evidence, approvals) live in the Hub database, never in memory.

## 7. Android / Termux status (rechecked 11/10/2026)

- Hermes 0.19.0 ships Termux install extras (`[termux]`, `[termux-all]`) and
  Termux-aware `hermes doctor` checks. Matrix E2EE and local faster-whisper are
  excluded on Termux.
- **It is still broken in practice for a clean install from PyPI.** The
  released 0.19.0 metadata says `Requires-Python: <3.14,>=3.11` (verified in
  the wheel), and current Termux ships Python 3.14.x.
  [hermes-agent#76288](https://github.com/NousResearch/hermes-agent/issues/76288)
  was closed on 07/10/2026 with the ceiling raised to `<3.15` on `main`
  (commit e1020f3, 18/09/2026), but no PyPI release containing it exists yet.
  The reporter's workaround is `--ignore-requires-python`.
- [hermes-agent#128831](https://github.com/NousResearch/hermes-agent/issues/128831)
  (opened 30/09/2026, **open**, last activity 10/10/2026): `hermes update`
  refuses git installs on Termux, and `hermes pm install` dead-ends because
  `playwright` has no Android wheel for Python 3.14. Fix PRs #128851 and
  #128824 are unmerged.
- [hermes-agent#124823](https://github.com/NousResearch/hermes-agent/issues/124823):
  `hermes update` cannot install managed `uv` on aarch64-linux-android.
- Search results report the official Termux docs page carrying a warning that
  the Termux package does not currently work (dated 25/09/2026 by a secondary
  guide). The docs site could not be fetched from the sandbox to quote it.

**Status for Atlas: BLOCKED on device.** Everything runnable in CI is built and
tested. Live items are marked BLOCKED in `docs/IMPLEMENTATION_STATUS.md` until
Hermes runs on the Pixel 7 Pro and the Hub's live verification button reports
PASS there. No substitute agent is used.

## 8. What Hermes does not provide (implemented in the Hub instead)

| Need | Where it is implemented |
|---|---|
| Approval engine with payload hash, expiry, single use, human-only approval | `hermes_hub_backend/approvals.py` (extended), `atlas/engine.py` |
| Telegram approve/deny buttons for Atlas actions | `atlas/telegram_approvals.py` (separate approval bot) |
| Catch-up after downtime, gap reporting, alert de-duplication | `atlas/checks.py`, `atlas/alerts.py` |
| Bill/car/commitment state machines and evidence matching | `atlas/state.py`, `atlas/evidence.py` |
| Startup check that fails loudly on a forbidden remote tool | `agent/atlas/install/check_policy.py` + `start-atlas-gateway.sh` |
| Skill review gate | `agent/atlas/skills.lock.json` + `atlas-hub get_skill` |
