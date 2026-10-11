#!/usr/bin/env python3
"""Install or update the Atlas Hermes profile. Dry run unless --apply.

Run with the Python of the Hermes installation, after
`hermes profile create atlas` has created the profile:

    ~/hermes-venv/bin/python agent/atlas/install/install_profile.py \
        --hub-python ~/teacher-app-hub/Apps/system/hermes-hub/backend/.venv/bin/python \
        --hub-data-dir ~/.hermes-hub \
        --telegram-chat-id 123456789 \
        --apply

What it writes (and nothing else):
  - <profile>/SOUL.md            copied verbatim from agent/atlas/persona/SOUL.md
  - <profile>/ATLAS_PROFILE      marker used by the policy check
  - <profile>/config.yaml        Atlas keys merged in; previous file kept as .bak
  - <profile>/cron/jobs.json     Atlas jobs (names "atlas:*") recreated via
                                 Hermes' create_job() with explicit toolsets
It never writes secrets: bot tokens and keys stay in <profile>/.env, which
Dan creates by hand with mode 0600.
"""

from __future__ import annotations

import argparse
import copy
import os
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import atlas_policy  # noqa: E402

SCHEDULES_PATH = atlas_policy.AGENT_DIR / "schedules.yaml"
JOB_PREFIX = "atlas:"
# Keys Atlas owns outright: replaced, not merged, so stale entries cannot
# survive and widen the policy.
OWNED_KEYS = ("timezone", "platform_toolsets", "mcp_servers")


def merge_config(existing: dict[str, Any], atlas: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(existing)
    for key, value in atlas.items():
        if key in OWNED_KEYS or not isinstance(value, dict) or not isinstance(merged.get(key), dict):
            merged[key] = copy.deepcopy(value)
        else:
            merged[key] = {**merged[key], **copy.deepcopy(value)}
    return merged


def load_schedules(path: Path = SCHEDULES_PATH) -> list[dict[str, Any]]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data["jobs"])


def main(argv: list[str] | None = None) -> int:
    import yaml

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hermes-root", type=Path, default=Path("~/.hermes"))
    parser.add_argument("--hub-python", required=True)
    parser.add_argument("--hub-data-dir", required=True)
    parser.add_argument("--telegram-chat-id", type=int)
    parser.add_argument("--deliver", default="telegram", help="cron delivery target")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args(argv)

    profile = args.hermes_root.expanduser() / "profiles" / "atlas"
    if not profile.is_dir():
        print(f"Profile {profile} does not exist. Run: hermes profile create atlas", file=sys.stderr)
        return 2

    atlas_cfg = atlas_policy.build_hermes_config(
        hub_python=args.hub_python,
        hub_data_dir=args.hub_data_dir,
        telegram_chat_id=args.telegram_chat_id,
    )
    config_path = profile / "config.yaml"
    existing = {}
    if config_path.is_file():
        existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    merged = merge_config(existing, atlas_cfg)
    violations = atlas_policy.check_static_config(merged)
    if violations:
        print("Refusing to install: the merged config violates policy:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    jobs = load_schedules()
    print(f"Profile: {profile}")
    print(f"SOUL.md  <- {atlas_policy.PERSONA_PATH}")
    print(f"config.yaml: set {sorted(atlas_cfg)}")
    for job in jobs:
        print(f"cron {JOB_PREFIX}{job['name']}: '{job['schedule']}' skills={job['skills']} toolsets={job['toolsets']}")
    if not args.apply:
        print("\nDry run only. Re-run with --apply to write these changes.")
        return 0

    shutil.copyfile(atlas_policy.PERSONA_PATH, profile / "SOUL.md")
    (profile / atlas_policy.PROFILE_MARKER).write_text("atlas\n", encoding="utf-8")
    if config_path.is_file():
        shutil.copyfile(config_path, config_path.with_suffix(".yaml.bak"))
    config_path.write_text(yaml.safe_dump(merged, sort_keys=True, allow_unicode=True), encoding="utf-8")
    os.chmod(config_path, 0o600)

    os.environ["HERMES_HOME"] = str(profile)
    from cron import jobs as cron_jobs  # type: ignore[import-not-found]

    with cron_jobs.use_cron_store(profile):
        for job in cron_jobs.list_jobs(include_disabled=True):
            if str(job.get("name", "")).startswith(JOB_PREFIX):
                cron_jobs.remove_job(job["id"])
        for job in jobs:
            cron_jobs.create_job(
                prompt=job["prompt"],
                schedule=job["schedule"],
                name=f"{JOB_PREFIX}{job['name']}",
                deliver=args.deliver,
                skills=list(job["skills"]),
                # Explicit allowlist: native toolsets plus the named MCP
                # servers (naming servers stops Hermes adding any others).
                enabled_toolsets=list(job["toolsets"]),
            )
    print("Installed. Now run check_policy.py before starting the gateway.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
