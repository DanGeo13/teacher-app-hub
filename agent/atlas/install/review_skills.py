#!/usr/bin/env python3
"""Show skill changes since Dan's last review, and record a review.

    python agent/atlas/install/review_skills.py            # list status
    python agent/atlas/install/review_skills.py --show NAME # print the skill
    python agent/atlas/install/review_skills.py --approve NAME --reviewer Dan

A skill that is new or changed is refused by atlas-hub (`get_skill`) and fails
the startup policy check until it is approved here. This covers skills that an
agent created or edited: they stay untrusted until a person reads them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import atlas_policy  # noqa: E402

# Text a skill must never contain: it may not try to change the persona, the
# tool policy or the approval rules, and may not carry credentials.
FORBIDDEN_SKILL_TEXT = (
    "SOUL.md",
    "tool-policy.json",
    "disabled_toolsets",
    "platform_toolsets",
    "approve_action",
    "skip approval",
    "without approval",
    "auto-approve",
    "ignore the persona",
)


def lint_skill(text: str) -> list[str]:
    problems = [f"mentions '{needle}'" for needle in FORBIDDEN_SKILL_TEXT if needle.lower() in text.lower()]
    if atlas_policy._SECRET_VALUE.search(text):
        problems.append("contains a credential-shaped value")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--show")
    parser.add_argument("--approve", action="append", default=[])
    parser.add_argument("--reviewer")
    parser.add_argument("--stage", action="store_true",
                        help="record current hashes as PENDING review (builder use; never approves)")
    args = parser.parse_args(argv)

    lock_path = atlas_policy.SKILLS_LOCK_PATH
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    current = atlas_policy.current_skill_hashes()

    if args.show:
        print((atlas_policy.SKILLS_DIR / args.show / "SKILL.md").read_text(encoding="utf-8"))
        return 0
    if args.stage:
        for name, digest in current.items():
            entry = lock["skills"].get(name)
            if entry is None or entry["sha256"] != digest:
                lock["skills"][name] = {"sha256": digest, "reviewedBy": None, "reviewedOn": None}
        lock["skills"] = dict(sorted(lock["skills"].items()))
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        print("Staged as pending review: " + ", ".join(sorted(current)))
        return 0
    if args.approve:
        if not args.reviewer:
            print("--reviewer is required", file=sys.stderr)
            return 2
        for name in args.approve:
            if name not in current:
                print(f"no skill folder named {name}", file=sys.stderr)
                return 2
            problems = lint_skill((atlas_policy.SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8"))
            if problems:
                print(f"{name} cannot be approved: {'; '.join(problems)}", file=sys.stderr)
                return 1
            lock["skills"][name] = {"sha256": current[name], "reviewedBy": args.reviewer,
                                    "reviewedOn": date.today().isoformat()}
        lock["skills"] = dict(sorted(lock["skills"].items()))
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        print("Recorded review for: " + ", ".join(args.approve))
        return 0

    for name, digest in current.items():
        entry = lock["skills"].get(name)
        if entry and entry["sha256"] == digest:
            state = "reviewed" if entry.get("reviewedBy") else "PENDING"
        else:
            state = "CHANGED" if entry else "NEW"
        print(f"{state:9} {name}")
    for name in lock["skills"]:
        if name not in current:
            print(f"MISSING   {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
