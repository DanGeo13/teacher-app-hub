"""Fixtures for the Atlas evaluation suite. Synthetic data only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas_helpers import AGENT_DIR, FIXTURES, MutableClock
from hermes_hub_backend.atlas.service import AtlasService
from hermes_hub_backend.database import Database


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "hub.db")


@pytest.fixture
def atlas(database: Database, clock: MutableClock, tmp_path: Path) -> AtlasService:
    return AtlasService(database, clock=clock, agent_dir=AGENT_DIR, backup_dir=tmp_path / "backups")


@pytest.fixture
def fixture_data():
    def load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load


@pytest.fixture
def new_bill(atlas):
    def make(**overrides):
        values = dict(title="Electricity", payee="Origin Energy", due_date="20/10/2026", amount="182.40",
                      reference="INV-77", source_account="gmail_personal", source_type="email")
        values.update(overrides)
        return atlas.propose_bill(**values)["record"]

    return make
