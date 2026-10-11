"""Atlas tool policy: Hermes profile config generation and startup checks.

Stdlib only (PyYAML is used when available for reading/writing config.yaml),
so this module runs both inside Hermes' own virtual environment on the Pixel
host and inside the Hub's test environment.

The policy itself lives in ``agent/atlas/policy/tool-policy.json``. Nothing in
here widens it: every function either renders it into Hermes' verified config
keys (see docs/AGENT_INTEGRATION.md) or reports a violation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parents[1]
POLICY_PATH = AGENT_DIR / "policy" / "tool-policy.json"
CONFIG_PATH = AGENT_DIR / "atlas.config.json"
SKILLS_DIR = AGENT_DIR / "skills"
SKILLS_LOCK_PATH = AGENT_DIR / "skills.lock.json"
PERSONA_PATH = AGENT_DIR / "persona" / "SOUL.md"
PROFILE_MARKER = "ATLAS_PROFILE"

# Values that look like an inline credential rather than a path or a flag.
_SECRET_VALUE = re.compile(
    r"(?i)(^sk-[A-Za-z0-9_-]{10,}|^gh[pousr]_[A-Za-z0-9]{20,}|^AIza[0-9A-Za-z_-]{10,}|"
    r"^ya29\.|^1//[0-9A-Za-z_-]{10,}|^xox[baprs]-|^[0-9]{6,}:[A-Za-z0-9_-]{30,}$|bearer\s)"
)
_SECRET_KEY = re.compile(r"(?i)(token|secret|password|api[_-]?key|credential|auth)")


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def model_facing_atlas_tools(policy: Mapping[str, Any] | None = None) -> list[str]:
    policy = policy or load_policy()
    tools = policy["atlasHubTools"]
    return sorted(set(tools["autonomous"]) | set(tools["approval_required"]))


# --------------------------------------------------------------------------
# Config generation
# --------------------------------------------------------------------------


def build_hermes_config(
    *,
    hub_python: str,
    hub_data_dir: str,
    agent_dir: str | None = None,
    secrets_dir: str = "~/.config/atlas/secrets",
    telegram_chat_id: int | None = None,
    policy: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render the Atlas Hermes profile config (the keys verified in Step 1)."""
    policy = policy or load_policy()
    config = config or load_config()
    agent_dir = agent_dir or str(AGENT_DIR)
    hermes = policy["hermes"]
    mail = policy["mailConnectors"]

    def mail_connector(name: str) -> dict[str, Any]:
        return {
            # Disabled until Dan installs and reviews a Gmail MCP server for
            # this account; enabling it is a deliberate local action.
            "enabled": False,
            "command": "REPLACE_WITH_REVIEWED_GMAIL_MCP_COMMAND",
            "args": [],
            "env": {"GMAIL_CREDENTIALS_FILE": f"{secrets_dir}/{name}.json"},
            "tools": {
                "include": list(mail["readOnlyToolAllowlist"]),
                "resources": False,
                "prompts": False,
            },
            "sampling": {"enabled": False},
        }

    topics = [
        {"name": "General"},
        {"name": "Bills", "skill": "bill-and-payment-monitor"},
        {"name": "Car", "skill": "car-maintenance-tracker"},
        {"name": "Payments", "skill": "payment-assistant"},
        {"name": "People", "skill": "social-relationship-assistant"},
        {"name": "Voice notes", "skill": "audio-action-capture"},
        {"name": "Weekly", "skill": "weekly-what-matters-report"},
        {"name": "System", "skill": "system-health-and-recovery"},
    ]

    rendered: dict[str, Any] = {
        "timezone": config["locale"]["timezone"],
        "agent": {
            "disabled_toolsets": sorted(
                ts for ts in hermes["forbiddenToolsets"] if not ts.startswith("hermes-")
            ),
        },
        "platform_toolsets": {
            platform: list(toolsets)
            for platform, toolsets in hermes["allowedNativeToolsets"].items()
        },
        "mcp_servers": {
            "atlas-hub": {
                "command": hub_python,
                "args": ["-m", "atlas_hub_mcp"],
                "env": {
                    "HUB_DATA_DIR": hub_data_dir,
                    "ATLAS_AGENT_DIR": agent_dir,
                },
                "tools": {
                    "include": model_facing_atlas_tools(policy),
                    "resources": False,
                    "prompts": False,
                },
                "sampling": {"enabled": False},
            },
            "gmail_personal": mail_connector("gmail_personal"),
            "gmail_work": mail_connector("gmail_work"),
        },
        "skills": {
            "external_dirs": [f"{agent_dir}/skills"],
            "inline_shell": False,
        },
        "memory": {"memory_enabled": True, "user_profile_enabled": True, "write_approval": True},
    }
    if telegram_chat_id is not None:
        rendered["platforms"] = {
            "telegram": {
                "extra": {"dm_topics": [{"chat_id": int(telegram_chat_id), "topics": topics}]}
            }
        }
    return rendered


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def _is_secret_like(key: str, value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if _SECRET_VALUE.search(value.strip()):
        return True
    # A credential-named key must point at a file, not hold the value.
    if _SECRET_KEY.search(key) and not key.upper().endswith(("_FILE", "_PATH", "_DIR")):
        return True
    return False


def check_static_config(
    cfg: Mapping[str, Any], policy: Mapping[str, Any] | None = None
) -> list[Violation]:
    """Checks that need only config.yaml (no Hermes import)."""
    policy = policy or load_policy()
    hermes = policy["hermes"]
    out: list[Violation] = []

    disabled = set((cfg.get("agent") or {}).get("disabled_toolsets") or [])
    needed = {ts for ts in hermes["forbiddenToolsets"] if not ts.startswith("hermes-")}
    missing = sorted(needed - disabled)
    if missing:
        out.append(Violation("DENYLIST_INCOMPLETE", f"agent.disabled_toolsets is missing {missing}"))

    platform_toolsets = cfg.get("platform_toolsets") or {}
    for platform in hermes["remotePlatforms"]:
        listed = platform_toolsets.get(platform)
        if not isinstance(listed, list):
            out.append(
                Violation(
                    "PLATFORM_UNCONFIGURED",
                    f"platform_toolsets.{platform} must be an explicit list "
                    "(otherwise Hermes falls back to its full default composite)",
                )
            )
            continue
        allowed = set(hermes["allowedNativeToolsets"].get(platform, []))
        allowed |= set(hermes["allowedMcpServers"])
        extra = sorted(set(map(str, listed)) - allowed)
        if extra:
            out.append(
                Violation("PLATFORM_TOOLSET_NOT_ALLOWED", f"platform_toolsets.{platform} lists {extra}")
            )

    servers = cfg.get("mcp_servers") or {}
    allowed_servers = set(hermes["allowedMcpServers"])
    for name, server in servers.items():
        if not isinstance(server, dict):
            out.append(Violation("MCP_INVALID", f"mcp_servers.{name} is not a mapping"))
            continue
        enabled = server.get("enabled", True) is not False
        if name not in allowed_servers and enabled:
            out.append(Violation("MCP_NOT_ALLOWED", f"mcp_servers.{name} is not in the Atlas allowlist"))
        tools = server.get("tools") or {}
        include = tools.get("include")
        if not include:
            out.append(
                Violation(
                    "MCP_NO_ALLOWLIST",
                    f"mcp_servers.{name}.tools.include must list tools explicitly",
                )
            )
            include = []
        if tools.get("resources", True) is not False or tools.get("prompts", True) is not False:
            out.append(Violation("MCP_UTILITIES_ON", f"mcp_servers.{name}: set tools.resources and tools.prompts to false"))
        if (server.get("sampling") or {}).get("enabled", True) is not False:
            out.append(Violation("MCP_SAMPLING_ON", f"mcp_servers.{name}: sampling.enabled must be false"))
        for key, value in (server.get("env") or {}).items():
            if _is_secret_like(str(key), value):
                out.append(
                    Violation(
                        "INLINE_SECRET",
                        f"mcp_servers.{name}.env.{key} looks like an inline credential; "
                        "pass a *_FILE path to a 0600 secret file instead",
                    )
                )
        for key in ("headers",):
            if server.get(key):
                out.append(Violation("INLINE_SECRET", f"mcp_servers.{name}.{key} must not carry credentials"))
        if name == "atlas-hub":
            forbidden = set(policy["atlasHubTools"]["forbidden"])
            bad = sorted(set(include) & forbidden)
            if bad:
                out.append(Violation("ATLAS_FORBIDDEN_TOOL", f"atlas-hub include lists forbidden {bad}"))
            unknown = sorted(set(include) - set(model_facing_atlas_tools(policy)))
            if unknown:
                out.append(Violation("ATLAS_UNKNOWN_TOOL", f"atlas-hub include lists unknown {unknown}"))
        elif name.startswith("gmail_"):
            mail = policy["mailConnectors"]
            for tool in include:
                lowered = str(tool).lower()
                if tool not in mail["readOnlyToolAllowlist"] or any(
                    pattern in lowered for pattern in mail["forbiddenToolPatterns"]
                ):
                    out.append(Violation("MAIL_WRITE_TOOL", f"mcp_servers.{name} includes non-read-only tool {tool!r}"))

    memory = cfg.get("memory") or {}
    if memory.get("write_approval") is not True:
        out.append(Violation("MEMORY_UNREVIEWED", "memory.write_approval must be true for Atlas"))
    if (cfg.get("skills") or {}).get("inline_shell"):
        out.append(Violation("SKILL_INLINE_SHELL", "skills.inline_shell must stay false"))
    return out


def effective_toolsets(
    platform: str,
    resolved: Iterable[str],
    cfg: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
) -> set[str]:
    """What Hermes actually gives an agent on ``platform``.

    ``resolved`` must come from Hermes' own ``_get_platform_tools``. The
    ``agent.disabled_toolsets`` denylist is subtracted only on platforms whose
    code path was verified to pass it (gateway messaging platforms, cron, CLI);
    the API server and ACP do not, so nothing is subtracted there.
    """
    policy = policy or load_policy()
    result = set(map(str, resolved))
    if platform in policy["hermes"]["platformsThatApplyDisabledToolsets"]:
        result -= set((cfg.get("agent") or {}).get("disabled_toolsets") or [])
    if platform == "cron":
        result -= {"cronjob", "messaging", "clarify"}
    return result


def check_resolved_tools(
    resolved_by_platform: Mapping[str, Iterable[str]],
    cfg: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
) -> list[Violation]:
    policy = policy or load_policy()
    hermes = policy["hermes"]
    enabled_mcp = {
        name
        for name, server in (cfg.get("mcp_servers") or {}).items()
        if not isinstance(server, dict) or server.get("enabled", True) is not False
    }
    out: list[Violation] = []
    for platform in hermes["remotePlatforms"]:
        if platform not in resolved_by_platform:
            out.append(Violation("PLATFORM_NOT_RESOLVED", f"{platform} toolsets were not resolved"))
            continue
        effective = effective_toolsets(platform, resolved_by_platform[platform], cfg, policy)
        allowed = set(hermes["allowedNativeToolsets"].get(platform, [])) | (
            set(hermes["allowedMcpServers"]) & enabled_mcp
        )
        forbidden = sorted(effective & set(hermes["forbiddenToolsets"]))
        if forbidden:
            out.append(Violation("FORBIDDEN_TOOLSET_ENABLED", f"{platform}: {forbidden}"))
        unexpected = sorted(effective - allowed - set(hermes["forbiddenToolsets"]))
        if unexpected:
            out.append(Violation("UNEXPECTED_TOOLSET_ENABLED", f"{platform}: {unexpected}"))
    return out


def check_tool_names(
    tool_names_by_platform: Mapping[str, Iterable[str]], policy: Mapping[str, Any] | None = None
) -> list[Violation]:
    """Check concrete tool names (e.g. from an agent's ``valid_tool_names``)."""
    policy = policy or load_policy()
    forbidden_tools = set(policy["hermes"]["forbiddenTools"])
    atlas_forbidden = {f"mcp__atlas_hub__{name}" for name in policy["atlasHubTools"]["forbidden"]}
    out = []
    for platform, names in tool_names_by_platform.items():
        names = set(map(str, names))
        bad = sorted(names & (forbidden_tools | atlas_forbidden))
        if bad:
            out.append(Violation("FORBIDDEN_TOOL_CALLABLE", f"{platform}: {bad}"))
    return out


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_skill_hashes(skills_dir: Path = SKILLS_DIR) -> dict[str, str]:
    hashes = {}
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        hashes[skill_md.parent.name] = sha256_file(skill_md)
    return hashes


def check_skills_lock(
    skills_dir: Path = SKILLS_DIR, lock_path: Path = SKILLS_LOCK_PATH
) -> list[Violation]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    reviewed = {name: entry["sha256"] for name, entry in lock["skills"].items()}
    current = current_skill_hashes(skills_dir)
    out = []
    for name, digest in current.items():
        if name not in reviewed:
            out.append(Violation("SKILL_UNREVIEWED", f"{name} is not in skills.lock.json"))
        elif reviewed[name] != digest:
            out.append(Violation("SKILL_MODIFIED", f"{name} changed since Dan's review"))
    for name in reviewed:
        if name not in current:
            out.append(Violation("SKILL_MISSING", f"{name} is locked but missing"))
    return out


def check_profile_environment(hermes_home: Path, default_root: Path | None = None) -> list[Violation]:
    """Host checks: secret file permissions, loopback API server, sticky profile."""
    out: list[Violation] = []
    if not (hermes_home / PROFILE_MARKER).is_file():
        out.append(Violation("NOT_ATLAS_PROFILE", f"{hermes_home} has no {PROFILE_MARKER} marker"))
    env_path = hermes_home / ".env"
    if env_path.exists():
        mode = stat.S_IMODE(env_path.stat().st_mode)
        if mode & 0o077:
            out.append(Violation("SECRET_FILE_PERMISSIONS", f"{env_path} is {oct(mode)}; must be 0600"))
        env = _read_env_file(env_path)
        host = env.get("API_SERVER_HOST", "127.0.0.1")
        if env.get("API_SERVER_ENABLED", "").lower() in {"1", "true", "yes", "on"} and host not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            out.append(Violation("API_SERVER_NOT_LOOPBACK", f"API_SERVER_HOST={host}"))
    root = default_root or hermes_home.parent.parent
    active = root / "active_profile"
    if active.is_file() and active.read_text(encoding="utf-8").strip() == "atlas":
        out.append(
            Violation(
                "ATLAS_IS_STICKY_DEFAULT",
                "active_profile is 'atlas': a plain `hermes acp`/`hermes` call would run Atlas "
                "with unrestricted ACP/CLI tools. Run `hermes profile use default`.",
            )
        )
    hub_home = os.environ.get("HERMES_HOME", "")
    if os.environ.get("HUB_HERMES_ENABLED", "").lower() in {"1", "true", "yes", "on"} and hub_home:
        if Path(hub_home).expanduser().resolve() == hermes_home.resolve():
            out.append(
                Violation(
                    "ACP_ON_ATLAS_PROFILE",
                    "The Hub's ACP chat would run against the Atlas profile; ACP ignores "
                    "platform_toolsets and agent.disabled_toolsets in Hermes 0.19.0.",
                )
            )
    return out


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values
