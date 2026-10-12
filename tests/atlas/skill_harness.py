"""Deterministic checks of each skill's declared trigger boundaries.

Hermes picks skills with the model (or by Telegram topic / cron binding), so
this is not a simulation of the model. It checks that the triggers and
"not_for" boundaries each SKILL.md declares are narrow and do not overlap:
a prompt meant for one skill must not be claimed by another, and unrelated
prompts must be claimed by none.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from atlas_helpers import AGENT_DIR


def load_skill(folder: Path) -> tuple[dict, str]:
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{folder.name}: SKILL.md must start with YAML frontmatter"
    _, frontmatter, body = text.split("---\n", 2)
    return yaml.safe_load(frontmatter), body


def all_skills() -> dict[str, tuple[dict, str]]:
    return {p.parent.name: load_skill(p.parent) for p in sorted((AGENT_DIR / "skills").glob("*/SKILL.md"))}


def route(prompt: str, skills: dict[str, tuple[dict, str]] | None = None) -> str | None:
    skills = skills or all_skills()
    lowered = f" {prompt.lower()} "
    scores = {}
    for name, (fm, _) in skills.items():
        atlas = fm["metadata"]["atlas"]
        if any(phrase.lower() in lowered for phrase in atlas["not_for"]):
            continue
        score = sum(1 for phrase in atlas["triggers"] if f"{phrase.lower()}" in lowered)
        if score:
            scores[name] = score
    if not scores:
        return None
    best = max(scores.values())
    winners = sorted(n for n, s in scores.items() if s == best)
    return winners[0] if len(winners) == 1 else "AMBIGUOUS:" + ",".join(winners)


def approved_agent_dir(tmp_path: Path) -> Path:
    """Copy agent/atlas and mark every skill reviewed by a test fixture."""
    target = tmp_path / "agent-atlas"
    shutil.copytree(AGENT_DIR, target)
    lock_path = target / "skills.lock.json"
    lock = json.loads(lock_path.read_text())
    for entry in lock["skills"].values():
        entry["reviewedBy"] = "test-fixture"
        entry["reviewedOn"] = "2026-10-11"
    lock_path.write_text(json.dumps(lock))
    return target
