"""Checks against a REAL Hermes installation (not a fake).

Set ATLAS_TEST_HERMES_PYTHON to the Python interpreter of a Hermes 0.19.x
install. Without it, every test here is reported BLOCKED (skipped with the
reason); none of them passes by default.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

import atlas_policy
from atlas_helpers import AGENT_DIR, ROOT
from hermes_hub_backend.database import Database

HERMES_PYTHON = os.environ.get("ATLAS_TEST_HERMES_PYTHON", "")
pytestmark = pytest.mark.skipif(
    not (HERMES_PYTHON and Path(HERMES_PYTHON).is_file()),
    reason="BLOCKED: real Hermes not available (set ATLAS_TEST_HERMES_PYTHON to a Hermes 0.19.x python)",
)


def run_hermes(code: str, home: Path, timeout: int = 180) -> str:
    env = {**os.environ, "HERMES_HOME": str(home)}
    proc = subprocess.run([HERMES_PYTHON, "-c", textwrap.dedent(code)], capture_output=True, text=True,
                          env=env, timeout=timeout, cwd=str(home))
    assert proc.returncode == 0, proc.stderr[-3000:]
    return proc.stdout.strip().splitlines()[-1]


def make_profile(tmp_path: Path, enable_atlas_hub: bool = True) -> Path:
    data_dir = tmp_path / "hub-data"
    Database(data_dir / "hub.db")
    home = tmp_path / ".hermes" / "profiles" / "atlas"
    home.mkdir(parents=True)
    config = atlas_policy.build_hermes_config(hub_python=sys.executable, hub_data_dir=str(data_dir),
                                              agent_dir=str(AGENT_DIR))
    config["mcp_servers"]["atlas-hub"]["enabled"] = enable_atlas_hub
    (home / "config.yaml").write_text(yaml.safe_dump(config))
    (home / "SOUL.md").write_bytes(atlas_policy.PERSONA_PATH.read_bytes())
    (home / atlas_policy.PROFILE_MARKER).write_text("atlas\n")
    return home


def test_persona_passes_hermes_injection_scanner_and_loads_verbatim(tmp_path):
    out = run_hermes(f"""
        import json
        from tools.threat_patterns import scan_for_threats
        from agent.prompt_builder import load_soul_md
        text = open({str(atlas_policy.PERSONA_PATH)!r}, encoding='utf-8').read()
        loaded = load_soul_md()
        print(json.dumps({{s: scan_for_threats(text, scope=s) for s in ('all','context','strict')}} | {{'verbatim': loaded == text.strip()}}))
    """, make_profile(tmp_path))
    result = json.loads(out)
    assert result == {"all": [], "context": [], "strict": [], "verbatim": True}


def test_hermes_resolver_gives_remote_platforms_no_forbidden_toolset(tmp_path):
    home = make_profile(tmp_path)
    proc = subprocess.run([HERMES_PYTHON, str(AGENT_DIR / "install" / "check_policy.py"), "--hermes-home",
                           str(home), "--json"], capture_output=True, text=True, timeout=300)
    report = json.loads(proc.stdout)
    by_name = {r["check"]: r for r in report["results"]}
    assert by_name["resolved remote toolsets (Hermes resolver)"]["status"] == "PASS", by_name
    assert by_name["skills discoverable by Hermes"]["status"] == "PASS", by_name
    assert proc.returncode == 0, proc.stderr


def test_hermes_mcp_client_discovers_atlas_hub_without_approval_tools(tmp_path):
    """Hermes' own MCP client connects to atlas-hub and registers its tools."""
    home = make_profile(tmp_path)
    out = run_hermes("""
        import json
        from tools.mcp_tool import discover_mcp_tools
        from tools.registry import registry
        discover_mcp_tools()
        names = sorted(n for n in registry.get_all_tool_names() if n.startswith('mcp__atlas_hub__'))
        print(json.dumps(names))
    """, home)
    names = {n.removeprefix("mcp__atlas_hub__") for n in json.loads(out)}
    assert names == set(atlas_policy.model_facing_atlas_tools())
    assert not names & set(atlas_policy.load_policy()["atlasHubTools"]["forbidden"])


def test_skill_frontmatter_parses_with_hermes(tmp_path):
    out = run_hermes(f"""
        import json, pathlib
        from agent.skill_utils import parse_frontmatter
        res = {{}}
        for p in sorted(pathlib.Path({str(AGENT_DIR / 'skills')!r}).glob('*/SKILL.md')):
            fm, body = parse_frontmatter(p.read_text(encoding='utf-8'))
            res[p.parent.name] = [fm.get('name'), len(fm.get('description', '')), len(body) > 200]
        print(json.dumps(res))
    """, make_profile(tmp_path))
    parsed = json.loads(out)
    assert parsed, "no skills found"
    for folder, (name, description_length, has_body) in parsed.items():
        assert name == folder
        assert 0 < description_length <= 1024
        assert has_body
