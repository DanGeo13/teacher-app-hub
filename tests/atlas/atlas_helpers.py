"""Helpers shared by the Atlas evaluation suite. Synthetic data only."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hermes_hub_backend.atlas.service import AtlasService

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "agent" / "atlas"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(AGENT_DIR / "install"))


class MutableClock:
    """Injectable UTC clock. Default: Mon 12/10/2026 10:00 Sydney (AEDT)."""

    def __init__(self, moment: datetime | None = None) -> None:
        self.moment = moment or datetime(2026, 10, 11, 23, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs) -> None:
        self.moment += timedelta(**kwargs)



def approve(atlas: AtlasService, approval_id: str, channel: str = "telegram", actor: str = "dan"):
    record = atlas.approvals.get(approval_id)
    return atlas.approvals.approve(approval_id, record.action, record.target, record.content_hash, actor, channel)


