"""Step 2 / 5: the tool-permission manifest is enforced, not just documented."""

from __future__ import annotations

import copy
import json
import os
import stat

import pytest

import atlas_policy
from atlas_hub_mcp.tools import TOOLS
from atlas_helpers import AGENT_DIR

POLICY = atlas_policy.load_policy()
APPROVAL_LIKE = {"approve", "deny", "reject", "decide", "sql", "query", "delete", "merge", "force", "set", "grant"}


def test_three_authority_levels_are_declared():
    assert set(POLICY["levels"]) == {"autonomous", "approval_required", "forbidden"}
    for section in ("atlasHubTools", "actions"):
        assert set(POLICY[section]) == {"autonomous", "approval_required", "forbidden"}
        levels = [set(POLICY[section][level]) for level in ("autonomous", "approval_required", "forbidden")]
        assert not (levels[0] & levels[1] or levels[0] & levels[2] or levels[1] & levels[2])


def test_model_facing_tools_exactly_match_policy():
    exposed = {tool.name: tool.level for tool in TOOLS}
    assert set(exposed) == set(atlas_policy.model_facing_atlas_tools(POLICY))
    for name, level in exposed.items():
        assert name in POLICY["atlasHubTools"][level], name


def test_no_forbidden_tool_is_callable():
    exposed = {tool.name for tool in TOOLS}
    assert not exposed & set(POLICY["atlasHubTools"]["forbidden"])
    for name in exposed:
        tokens = set(name.split("_"))
        assert not tokens & APPROVAL_LIKE, f"{name} looks like an approval/SQL/delete tool"


def test_forbidden_policy_actions_cannot_be_approved_for_execution(atlas):
    from hermes_hub_backend.atlas.service import FORBIDDEN_ACTIONS

    assert set(POLICY["actions"]["forbidden"]) <= FORBIDDEN_ACTIONS | {"external_transfer"}


def _config():
    return atlas_policy.build_hermes_config(hub_python="/usr/bin/python3", hub_data_dir="/srv/hub",
                                            telegram_chat_id=123456789)


def test_generated_hermes_config_passes_static_policy():
    assert atlas_policy.check_static_config(_config()) == []


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda c: c["platform_toolsets"]["telegram"].append("terminal"), "PLATFORM_TOOLSET_NOT_ALLOWED"),
        (lambda c: c["platform_toolsets"]["api_server"].append("browser"), "PLATFORM_TOOLSET_NOT_ALLOWED"),
        (lambda c: c["agent"]["disabled_toolsets"].remove("file"), "DENYLIST_INCOMPLETE"),
        (lambda c: c["platform_toolsets"].pop("cron"), "PLATFORM_UNCONFIGURED"),
        (lambda c: c["mcp_servers"]["gmail_personal"]["tools"]["include"].append("send_message"), "MAIL_WRITE_TOOL"),
        (lambda c: c["mcp_servers"]["gmail_work"]["tools"].pop("include"), "MCP_NO_ALLOWLIST"),
        (lambda c: c["mcp_servers"]["atlas-hub"]["tools"]["include"].append("approve_action"), "ATLAS_FORBIDDEN_TOOL"),
        (lambda c: c["mcp_servers"]["gmail_personal"]["env"].update({"GMAIL_TOKEN": "ya29.secretvalue"}), "INLINE_SECRET"),
        (lambda c: c["mcp_servers"].update({"filesystem": {"command": "npx", "tools": {"include": ["x"]}}}), "MCP_NOT_ALLOWED"),
        (lambda c: c["mcp_servers"]["atlas-hub"].update({"sampling": {"enabled": True}}), "MCP_SAMPLING_ON"),
        (lambda c: c["memory"].update({"write_approval": False}), "MEMORY_UNREVIEWED"),
        (lambda c: c["skills"].update({"inline_shell": True}), "SKILL_INLINE_SHELL"),
    ],
)
def test_static_check_fails_loudly_on_widening(mutate, code):
    config = copy.deepcopy(_config())
    mutate(config)
    codes = {v.code for v in atlas_policy.check_static_config(config)}
    assert code in codes


def test_resolved_tool_check_rejects_forbidden_toolsets_per_platform():
    config = _config()
    good = {"telegram": {"memory", "cronjob", "atlas-hub", "kanban"},  # kanban is denylisted on telegram
            "api_server": {"memory", "cronjob", "atlas-hub"},
            "cron": {"memory", "atlas-hub", "kanban"}}
    assert atlas_policy.check_resolved_tools(good, config) == []
    bad = copy.deepcopy(good)
    bad["api_server"].add("terminal")  # api_server does NOT apply agent.disabled_toolsets
    violations = atlas_policy.check_resolved_tools(bad, config)
    assert [v.code for v in violations] == ["FORBIDDEN_TOOLSET_ENABLED"]
    assert "api_server" in violations[0].message


def test_tool_name_check_catches_callable_forbidden_tool():
    assert atlas_policy.check_tool_names({"telegram": ["memory", "mcp__atlas_hub__list_obligations"]}) == []
    violations = atlas_policy.check_tool_names({"telegram": ["memory", "terminal"],
                                                "cron": ["mcp__atlas_hub__approve_action"]})
    assert {v.code for v in violations} == {"FORBIDDEN_TOOL_CALLABLE"}
    assert len(violations) == 2


def test_profile_environment_checks(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "atlas"
    profile.mkdir(parents=True)
    assert "NOT_ATLAS_PROFILE" in {v.code for v in atlas_policy.check_profile_environment(profile)}
    (profile / atlas_policy.PROFILE_MARKER).write_text("atlas\n")
    env = profile / ".env"
    env.write_text("API_SERVER_ENABLED=true\nAPI_SERVER_HOST=0.0.0.0\n")
    env.chmod(0o644)
    (root / "active_profile").write_text("atlas\n")
    monkeypatch.setenv("HUB_HERMES_ENABLED", "true")
    monkeypatch.setenv("HERMES_HOME", str(profile))
    codes = {v.code for v in atlas_policy.check_profile_environment(profile)}
    assert codes == {"SECRET_FILE_PERMISSIONS", "API_SERVER_NOT_LOOPBACK", "ATLAS_IS_STICKY_DEFAULT", "ACP_ON_ATLAS_PROFILE"}
    env.chmod(0o600)
    env.write_text("API_SERVER_ENABLED=true\nAPI_SERVER_HOST=127.0.0.1\n")
    (root / "active_profile").write_text("default\n")
    monkeypatch.delenv("HUB_HERMES_ENABLED")
    assert atlas_policy.check_profile_environment(profile) == []


def test_check_policy_cli_exits_nonzero_on_violation(tmp_path, capsys):
    import check_policy
    import yaml

    profile = tmp_path / "profiles" / "atlas"
    profile.mkdir(parents=True)
    (profile / atlas_policy.PROFILE_MARKER).write_text("atlas\n")
    (profile / "SOUL.md").write_bytes(atlas_policy.PERSONA_PATH.read_bytes())
    config = _config()
    config["platform_toolsets"]["telegram"].append("terminal")
    (profile / "config.yaml").write_text(yaml.safe_dump(config))
    assert check_policy.main(["--hermes-home", str(profile), "--static-only"]) == 1
    assert "ATLAS POLICY CHECK FAILED" in capsys.readouterr().err

    config["platform_toolsets"]["telegram"].remove("terminal")
    (profile / "config.yaml").write_text(yaml.safe_dump(config))
    assert check_policy.main(["--hermes-home", str(profile), "--static-only", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    blocked = [r for r in report["results"] if r["status"] == "BLOCKED"]
    assert blocked, "static-only mode must report Hermes-dependent checks as BLOCKED, not PASS"


def test_persona_file_is_the_verbatim_source_of_truth():
    text = (AGENT_DIR / "persona" / "SOUL.md").read_text(encoding="utf-8")
    assert text.startswith('You are "Atlas", Dan\'s personal operations agent.')
    for heading in ("TIMEZONE AND LOCALE", "TRUST AND PROMPT-INJECTION RULES", "AUTHORITY LEVELS",
                    "APPROVAL PROTOCOL", "STATE MODEL", "EVIDENCE MATCHING", "QUIET OPERATION",
                    "ESCALATION", "AUDIO", "CAPABILITY HONESTY", "MEMORY", "RESPONSE FORMAT BY CHANNEL"):
        assert f"\n{heading}\n" in text
    config = json.loads((AGENT_DIR / "atlas.config.json").read_text())
    assert config["locale"] == {"timezone": "Australia/Sydney", "dateFormat": "DD/MM/YYYY",
                                "currency": "AUD", "language": "en-AU"}


def test_installer_scripts_are_executable_shell_safe():
    script = AGENT_DIR / "install" / "start-atlas-gateway.sh"
    content = script.read_text()
    assert "set -euo pipefail" in content
    assert "check_policy.py" in content and content.index("check_policy.py") < content.index("gateway run")
