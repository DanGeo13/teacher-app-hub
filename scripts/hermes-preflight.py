#!/usr/bin/env python3
"""Read-only preflight for the actual Hermes/Ollama/Hub runtime on this host.

Run this from inside the target environment (e.g. the user's Codespace),
with the backend's virtualenv active, so it can import the Hub's own
settings and ACP client code rather than re-implementing diagnostics:

    cd Apps/system/hermes-hub/backend
    .venv/bin/python3 ../../../../scripts/hermes-preflight.py

What this checks, all read-only:
  - Hermes executable resolution on PATH and `hermes --version`.
  - A single short-lived ACP `initialize` handshake: the Hermes subprocess
    is started and asked to report its capabilities, then terminated.
    `session/new` / `session/resume` / `session/prompt` are never called,
    so this never creates a Hermes-side session, never selects a model, and
    never sends a prompt.
  - Ollama reachability (`/api/version`) and the identity of whatever
    models are already installed (`/api/tags`) — a listing, not a pull.
  - Whether Hermes' own state directory (`$HERMES_HOME` or `~/.hermes`)
    exists, and its top-level entry *names* only — never file contents.
  - The Hub's own HTTPS/auth-relevant configuration as it would start with
    (cookie security mode, allowed origins, whether the admin password is
    configured) — never the password value itself.

What this explicitly does NOT do:
  - Does not download, pull, or switch any model.
  - Does not change HERMES_HOME, provider configuration, or Ollama's
    default/selected model.
  - Does not rebuild, reinstall, or reconfigure the Codespace, Hermes, or
    Ollama.
  - Does not move, copy, back up, or open the contents of Hermes' state
    directory.
  - Does not open a Hermes session or send any prompt. A real synthetic-data
    text exchange through the actual Hermes/Qwen stack is a separate,
    explicitly gated action (see the trailing note this script prints, and
    docs/hermes-sessions.md); it stays BLOCKED until that is deliberately
    attempted with the Hub's normal HUB_HERMES_ENABLED=true opt-in.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "Apps" / "system" / "hermes-hub" / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from hermes_hub_backend.hermes_acp import AcpHermesClient, HermesUnavailableError  # noqa: E402
from hermes_hub_backend.settings import ConfigurationError, Settings  # noqa: E402


@dataclass
class Section:
    name: str
    ok: bool
    details: dict = field(default_factory=dict)


def check_hermes_binary(executable: str, timeout: float) -> Section:
    resolved = shutil.which(executable)
    if resolved is None:
        return Section(
            "hermes_binary",
            False,
            {
                "configured_executable": executable,
                "resolved_path": None,
                "version": None,
                "note": "not found on PATH",
            },
        )
    version = None
    note = None
    try:
        result = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or result.stderr).strip().splitlines()
        if result.returncode == 0 and output:
            version = output[0][:160]
        else:
            note = f"--version exited {result.returncode}"
    except (OSError, subprocess.SubprocessError) as error:
        note = f"--version failed: {error}"
    return Section(
        "hermes_binary",
        version is not None,
        {
            "configured_executable": executable,
            "resolved_path": resolved,
            "version": version,
            "note": note,
        },
    )


async def check_acp_handshake(
    executable: str, workspace_dir: Path, startup_timeout: float
) -> Section:
    """Spawn `hermes acp`, complete only the `initialize` handshake, then
    terminate the process immediately. No session/new call is ever made, so
    this never creates a Hermes-side session."""
    client = AcpHermesClient(
        executable=executable,
        workspace_dir=workspace_dir,
        startup_timeout_seconds=startup_timeout,
        turn_timeout_seconds=startup_timeout,
    )
    try:
        info = await client.start()
    except HermesUnavailableError as error:
        return Section(
            "acp_handshake",
            False,
            {
                "reason": error.diagnostics.reason,
                "stderr_tail": error.diagnostics.stderr_tail,
            },
        )
    finally:
        await client.aclose()
    return Section(
        "acp_handshake",
        True,
        {
            "protocol_version": info.protocol_version,
            "agent_name": info.name,
            "agent_version": info.version,
            "advertises_session_load": info.load_session,
            "advertises_session_resume": info.can_resume,
        },
    )


def _get_json(url: str, timeout: float) -> tuple[bool, object, str | None]:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(256_000))
            return True, payload, None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        return False, None, str(error)


def check_ollama(base_url: str, timeout: float) -> Section:
    ok_version, version_payload, version_error = _get_json(f"{base_url}/api/version", timeout)
    ok_tags, tags_payload, tags_error = _get_json(f"{base_url}/api/tags", timeout)
    models: list[str] = []
    if ok_tags and isinstance(tags_payload, dict):
        for model in tags_payload.get("models", []):
            if isinstance(model, dict) and isinstance(model.get("name"), str):
                models.append(model["name"])
    return Section(
        "ollama",
        ok_version,
        {
            "endpoint": base_url,
            "reachable": ok_version,
            "version": (
                version_payload.get("version")
                if ok_version and isinstance(version_payload, dict)
                else None
            ),
            "version_error": None if ok_version else version_error,
            # A listing of already-installed models, never a pull/download.
            "installed_models": models,
            "tags_error": None if ok_tags else tags_error,
        },
    )


def check_hermes_state_dir() -> Section:
    override = os.environ.get("HERMES_HOME", "").strip()
    state_dir = Path(override).expanduser() if override else Path.home() / ".hermes"
    exists = state_dir.exists()
    entries: list[str] = []
    if exists and state_dir.is_dir():
        try:
            # Names only, never contents: this reports what is present, it
            # never reads, copies, or moves anything inside this directory.
            entries = sorted(path.name for path in state_dir.iterdir())
        except OSError:
            entries = []
    return Section(
        "hermes_state_dir",
        exists,
        {
            "path": str(state_dir),
            "source": "HERMES_HOME" if override else "default (~/.hermes)",
            "exists": exists,
            "top_level_entries": entries,
        },
    )


def check_hub_config() -> Section:
    try:
        settings = Settings.from_env()
    except ConfigurationError as error:
        return Section(
            "hub_config",
            False,
            {"configured": False, "reason": str(error)},
        )
    return Section(
        "hub_config",
        True,
        {
            "configured": True,
            "data_dir": str(settings.data_dir),
            "data_dir_exists": settings.data_dir.exists(),
            "cookie_secure": settings.cookie_secure,
            "cookie_name": settings.cookie_name,
            "allowed_origins": list(settings.allowed_origins),
            "hermes_enabled": settings.hermes_enabled,
            "hermes_executable": settings.hermes_executable,
            "hermes_workspace_dir": str(settings.hermes_workspace),
            "session_ttl_seconds": settings.session_ttl_seconds,
            # Presence only — never the password value itself.
            "admin_password_configured": bool(settings.admin_password),
        },
    )


def print_summary(sections: list[Section]) -> None:
    print(
        "Hermes Hub runtime preflight — read-only; nothing installed, "
        "downloaded, pulled, or reconfigured.\n"
    )
    for section in sections:
        status = "OK" if section.ok else "NOT READY"
        print(f"[{status}] {section.name}")
        for key, value in section.details.items():
            print(f"    {key}: {value}")
        print()
    print(
        "No Hermes session was created and no prompt was sent by this preflight\n"
        "(session/new, session/resume and session/prompt are never called here).\n"
        "A real synthetic-data text exchange through the actual Hermes/Qwen stack\n"
        "is a separate, explicitly gated action — it requires HUB_HERMES_ENABLED=true\n"
        "and a session started through the normal Hub API, not this script. See\n"
        "docs/hermes-sessions.md for what that path checks and records. Treat that\n"
        "as BLOCKED until it is deliberately exercised and its result is reported\n"
        "honestly, separately from this preflight."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--json", action="store_true", help="print a machine-readable JSON report"
    )
    parser.add_argument(
        "--hermes-executable", default=os.environ.get("HERMES_EXECUTABLE", "hermes")
    )
    parser.add_argument(
        "--ollama-base-url",
        default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="seconds to wait for the ACP handshake and each HTTP probe",
    )
    args = parser.parse_args()

    sections = [
        check_hermes_binary(args.hermes_executable, args.timeout),
        asyncio.run(
            check_acp_handshake(args.hermes_executable, Path.cwd(), args.timeout)
        ),
        check_ollama(args.ollama_base_url, args.timeout),
        check_hermes_state_dir(),
        check_hub_config(),
    ]

    if args.json:
        print(json.dumps({section.name: {"ok": section.ok, **section.details} for section in sections}, indent=2))
    else:
        print_summary(sections)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
