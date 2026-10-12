#!/usr/bin/env python3
"""Run the Atlas evaluation suite and print PASS / FAIL / BLOCKED per test.

    Apps/system/hermes-hub/backend/.venv/bin/python scripts/run-atlas-evals.py [--report out.md]

Real-Hermes compatibility tests run only when ATLAS_TEST_HERMES_PYTHON points
at a Hermes 0.19.x interpreter; otherwise they are reported BLOCKED (never
PASS). Exit status is non-zero if anything FAILED.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {
    "test_eval_core": "Core guarantees (evidence, injection, approvals, state machines, audio, digest, redaction)",
    "test_policy": "Tool policy and startup check",
    "test_mcp_server": "atlas-hub MCP server",
    "test_skills": "Skills",
    "test_schedules": "Schedules, catch-up and daylight saving",
    "test_approval_channels": "Approval channels and live verification",
    "test_hermes_compat": "Real Hermes compatibility",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, help="also write a Markdown report here")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "atlas.xml"
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests" / "atlas"),
                        f"--junitxml={junit}"], cwd=ROOT, capture_output=True, text=True)
        if not junit.exists():
            print("The evaluation suite did not run (no junit report produced).", file=sys.stderr)
            return 2
        tree = ET.parse(junit)

    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for case in tree.iter("testcase"):
        module = case.get("classname", "").split(".")[-1]
        name = case.get("name", "")
        if case.find("failure") is not None or case.find("error") is not None:
            node = case.find("failure") if case.find("failure") is not None else case.find("error")
            status, detail = "FAIL", (node.get("message") or "")[:160]
        elif case.find("skipped") is not None:
            status, detail = "BLOCKED", (case.find("skipped").get("message") or "")[:160]
        else:
            status, detail = "PASS", ""
        groups[module].append((name, status, detail))

    lines = ["# Atlas evaluation report", ""]
    totals = defaultdict(int)
    for module in list(CATEGORIES) + sorted(set(groups) - set(CATEGORIES)):
        if module not in groups:
            continue
        lines += [f"## {CATEGORIES.get(module, module)}", ""]
        for name, status, detail in groups[module]:
            totals[status] += 1
            lines.append(f"- **{status}** `{name}`" + (f" — {detail}" if detail else ""))
        lines.append("")
    summary = f"PASS {totals['PASS']} · FAIL {totals['FAIL']} · BLOCKED {totals['BLOCKED']}"
    lines.insert(2, summary)
    lines.insert(3, "")
    text = "\n".join(lines)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    return 1 if totals["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
