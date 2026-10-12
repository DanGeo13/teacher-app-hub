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


def make_profile(tmp_path: Path, enable_atlas_hub: bool = True, agent_dir: Path = AGENT_DIR) -> Path:
    data_dir = tmp_path / "hub-data"
    Database(data_dir / "hub.db")
    home = tmp_path / ".hermes" / "profiles" / "atlas"
    home.mkdir(parents=True)
    config = atlas_policy.build_hermes_config(hub_python=sys.executable, hub_data_dir=str(data_dir),
                                              agent_dir=str(agent_dir))
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
    from skill_harness import approved_agent_dir

    reviewed = approved_agent_dir(tmp_path)
    home = make_profile(tmp_path, agent_dir=reviewed)
    proc = subprocess.run([HERMES_PYTHON, str(reviewed / "install" / "check_policy.py"), "--hermes-home",
                           str(home), "--json"], capture_output=True, text=True, timeout=300,
                          env={**os.environ, "ATLAS_AGENT_DIR": str(reviewed)})
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


def test_installer_creates_cron_jobs_with_explicit_toolsets_in_real_hermes(tmp_path):
    from skill_harness import approved_agent_dir

    reviewed = approved_agent_dir(tmp_path)
    root = tmp_path / "root" / ".hermes"
    (root / "profiles" / "atlas").mkdir(parents=True)
    data_dir = tmp_path / "hub-data"
    Database(data_dir / "hub.db")
    env = {**os.environ, "ATLAS_AGENT_DIR": str(reviewed), "HOME": str(tmp_path / "root")}
    proc = subprocess.run([HERMES_PYTHON, str(reviewed / "install" / "install_profile.py"), "--hermes-root", str(root),
                           "--hub-python", sys.executable, "--hub-data-dir", str(data_dir), "--apply"],
                          capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    home = root / "profiles" / "atlas"
    out = run_hermes("""
        import json
        from cron import jobs
        with jobs.use_cron_store(__import__('os').environ['HERMES_HOME']):
            listed = jobs.list_jobs(include_disabled=True)
        print(json.dumps([{k: j.get(k) for k in ('name', 'skills', 'enabled_toolsets', 'next_run_at', 'schedule')} for j in listed]))
    """, home)
    jobs = {j["name"]: j for j in json.loads(out)}
    assert set(jobs) == {f"atlas:{j['name']}" for j in atlas_policy.load_schedules()}
    for job in jobs.values():
        assert job["enabled_toolsets"] and "terminal" not in job["enabled_toolsets"]
    weekly = jobs["atlas:weekly-digest"]
    from datetime import datetime
    from zoneinfo import ZoneInfo

    nxt = datetime.fromisoformat(weekly["next_run_at"]).astimezone(ZoneInfo("Australia/Sydney"))
    # The job wakes hourly on Sunday mornings; the Hub gate releases the digest at 08:00.
    assert nxt.strftime("%a") == "Sun" and 7 <= nxt.hour <= 11 and nxt.minute == 0
    check = subprocess.run([HERMES_PYTHON, str(reviewed / "install" / "check_policy.py"), "--hermes-home", str(home)],
                           capture_output=True, text=True, env=env, timeout=300)
    assert check.returncode == 0, check.stdout + check.stderr


def test_hermes_cron_plus_hub_gate_sends_one_digest_at_0800_across_dst(tmp_path, atlas, clock):
    """Drive Hermes' REAL next-run calculation for the weekly job week by week
    across both 2026/27 daylight-saving changes, feeding each run time to the
    Hub gate. Exactly one digest per Sunday must be released, at 08:00 local."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    out = run_hermes("""
        import json, os
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from cron.jobs import compute_next_run, parse_schedule
        sched = parse_schedule('0 7-11 * * 0')
        last = datetime(2026, 9, 13, 11, 0, tzinfo=ZoneInfo('Australia/Sydney')).isoformat()
        runs = []
        for _ in range(5 * 33):
            nxt = compute_next_run(sched, last)
            runs.append(nxt)
            # Hermes stores the actual run time in its configured timezone.
            last = datetime.fromisoformat(nxt).astimezone(ZoneInfo('Australia/Sydney')).isoformat()
        print(json.dumps(runs))
    """, make_profile(tmp_path))
    sydney = ZoneInfo("Australia/Sydney")
    released = []
    for value in json.loads(out):
        clock.moment = datetime.fromisoformat(value).astimezone(UTC)
        if atlas.get_check_status("weekly_digest").get("digestDueNow"):
            atlas.log_check(check_name="weekly_digest", outcome="ok")
            released.append(clock.moment.astimezone(sydney))
    assert len(released) == len({r.date() for r in released}), "at most one digest per Sunday"
    assert len(released) >= 30
    assert all(r.strftime("%a %H:%M") == "Sun 08:00" for r in released), [r.isoformat() for r in released if r.hour != 8]
    dates = {r.date().isoformat() for r in released}
    assert {"2026-10-04", "2027-04-04"} <= dates, "both daylight-saving Sundays get their 08:00 digest"


def test_hermes_raw_cron_anchor_is_offset_based_documented_upstream_issue(tmp_path):
    """Records the Hermes 0.19.0 behaviour that the Hub gate works around.

    If Hermes fixes it, this test fails and the workaround can be revisited.
    """
    out = run_hermes("""
        import json, os
        os.environ['HERMES_TIMEZONE'] = 'Australia/Sydney'
        import hermes_time; hermes_time.reset_cache() if hasattr(hermes_time, 'reset_cache') else None
        from cron.jobs import compute_next_run, parse_schedule
        sched = parse_schedule('0 8 * * 0')
        res = [compute_next_run(sched, last) for last in ('2026-09-27T09:00:00+10:00', '2027-03-28T09:00:00+11:00')]
        print(json.dumps(res))
    """, make_profile(tmp_path))
    from datetime import UTC, datetime

    first, second = (datetime.fromisoformat(v).astimezone(UTC) for v in json.loads(out))
    # A naive "0 8 * * 0" job lands at 07:00 AEDT and 09:00 AEST on the DST Sundays.
    assert first == datetime(2026, 10, 3, 20, 0, tzinfo=UTC)
    assert second == datetime(2027, 4, 3, 23, 0, tzinfo=UTC)
