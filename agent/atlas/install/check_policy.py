#!/usr/bin/env python3
"""Atlas startup policy check. Fails loudly (exit 1) on any violation.

Run with the Python interpreter of the Hermes installation so the check uses
Hermes' own toolset resolver rather than re-implementing it:

    ~/hermes-venv/bin/python agent/atlas/install/check_policy.py \
        --hermes-home ~/.hermes/profiles/atlas

``--json`` prints a machine-readable report (used by the Hub's live
verification). ``--static-only`` skips the parts that need Hermes importable;
those parts are then reported as BLOCKED, never as passing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import atlas_policy  # noqa: E402

RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
RESULT_BLOCKED = "BLOCKED"


def _load_yaml(path: Path) -> dict:
    import yaml  # Hermes depends on PyYAML; the Hub test env pins it too.

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping")
    return data


def _resolve_with_hermes(hermes_home: Path, platforms: list[str]) -> dict[str, set[str]]:
    # HERMES_HOME must be set before any Hermes module is imported: several
    # modules resolve paths at import time.
    os.environ["HERMES_HOME"] = str(hermes_home)
    from hermes_cli.config import load_config  # type: ignore[import-not-found]
    from hermes_cli.tools_config import _get_platform_tools  # type: ignore[import-not-found]

    cfg = load_config()
    return {platform: set(_get_platform_tools(cfg, platform)) for platform in platforms}


def _discover_skills_with_hermes(hermes_home: Path) -> set[str]:
    os.environ["HERMES_HOME"] = str(hermes_home)
    from tools.skills_tool import skills_list  # type: ignore[import-not-found]

    payload = json.loads(skills_list())
    return {entry["name"] for entry in payload.get("skills", [])}


def run(hermes_home: Path, static_only: bool = False) -> list[dict]:
    policy = atlas_policy.load_policy()
    results: list[dict] = []

    def record(name: str, status: str, detail: str = "") -> None:
        results.append({"check": name, "status": status, "detail": detail})

    config_path = hermes_home / "config.yaml"
    if not config_path.is_file():
        record("config.yaml present", RESULT_FAIL, f"{config_path} not found")
        return results
    cfg = _load_yaml(config_path)

    violations = atlas_policy.check_static_config(cfg, policy)
    record(
        "static tool policy",
        RESULT_FAIL if violations else RESULT_PASS,
        "; ".join(map(str, violations)),
    )

    env_violations = atlas_policy.check_profile_environment(hermes_home)
    record(
        "profile environment",
        RESULT_FAIL if env_violations else RESULT_PASS,
        "; ".join(map(str, env_violations)),
    )

    soul = hermes_home / "SOUL.md"
    if not soul.is_file():
        record("persona installed", RESULT_FAIL, f"{soul} missing")
    elif atlas_policy.sha256_file(soul) != atlas_policy.sha256_file(atlas_policy.PERSONA_PATH):
        record("persona installed", RESULT_FAIL, "SOUL.md differs from agent/atlas/persona/SOUL.md")
    else:
        record("persona installed", RESULT_PASS)

    lock_violations = atlas_policy.check_skills_lock()
    record(
        "skills match Dan's review lock",
        RESULT_FAIL if lock_violations else RESULT_PASS,
        "; ".join(map(str, lock_violations)),
    )

    if static_only:
        record("resolved remote toolsets (Hermes resolver)", RESULT_BLOCKED, "--static-only")
        record("skills discoverable by Hermes", RESULT_BLOCKED, "--static-only")
        return results

    try:
        resolved = _resolve_with_hermes(hermes_home, list(policy["hermes"]["remotePlatforms"]))
    except ImportError as error:
        record(
            "resolved remote toolsets (Hermes resolver)",
            RESULT_BLOCKED,
            f"Hermes is not importable from {sys.executable}: {error}",
        )
    else:
        tool_violations = atlas_policy.check_resolved_tools(resolved, cfg, policy)
        record(
            "resolved remote toolsets (Hermes resolver)",
            RESULT_FAIL if tool_violations else RESULT_PASS,
            "; ".join(map(str, tool_violations))
            or json.dumps({k: sorted(atlas_policy.effective_toolsets(k, v, cfg, policy)) for k, v in resolved.items()}),
        )

    try:
        discovered = _discover_skills_with_hermes(hermes_home)
    except ImportError as error:
        record("skills discoverable by Hermes", RESULT_BLOCKED, str(error))
    else:
        expected = set(atlas_policy.current_skill_hashes())
        missing = sorted(expected - discovered)
        record(
            "skills discoverable by Hermes",
            RESULT_FAIL if missing else RESULT_PASS,
            f"missing {missing}" if missing else f"{len(expected)} skills",
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hermes-home", required=True, type=Path)
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results = run(args.hermes_home.expanduser().resolve(), static_only=args.static_only)
    failed = [r for r in results if r["status"] == RESULT_FAIL]
    if args.json:
        print(json.dumps({"results": results, "ok": not failed}, indent=2))
    else:
        for r in results:
            line = f"[{r['status']}] {r['check']}"
            print(line + (f" — {r['detail']}" if r["detail"] else ""))
    if failed:
        banner = "!" * 72
        print(
            f"\n{banner}\nATLAS POLICY CHECK FAILED: {len(failed)} check(s). "
            f"Do NOT start the Atlas gateway.\n{banner}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
