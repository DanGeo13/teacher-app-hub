#!/usr/bin/env python3
"""Conservative repository and frontend-bundle secret scan; not a DLP guarantee."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "OpenAI-style secret": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "Google OAuth refresh token": re.compile(rb"\b1//[0-9A-Za-z_-]{20,}\b"),
}


def files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == ".env.example" or path.stat().st_size > 5_000_000:
            continue
        yield path


def main() -> int:
    findings: list[str] = []
    for path in files():
        try:
            content = path.read_bytes()
        except OSError:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{path.relative_to(ROOT)}: possible {label}")
    if findings:
        print("Secret scan failed:", *findings, sep="\n- ", file=sys.stderr)
        return 1
    print("Secret scan passed: no configured high-confidence patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
