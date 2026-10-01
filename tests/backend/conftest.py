from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hermes_hub_backend.api import create_app
from hermes_hub_backend.settings import Settings

ORIGIN = "http://testserver"
PASSWORD = "synthetic-test-password-1234"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "runtime",
        admin_password=PASSWORD,
        cookie_secure=False,
        allowed_origins=(ORIGIN,),
        hermes_dashboard_url="http://127.0.0.1:1",
        hermes_executable="definitely-not-installed-hermes",
        ollama_base_url="http://127.0.0.1:1",
        runtime_probe_timeout_seconds=0.2,
        frontend_dist=tmp_path / "missing-dist",
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def secure_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "secure-runtime",
        admin_password=PASSWORD,
        cookie_secure=True,
        allowed_origins=(ORIGIN,),
        hermes_dashboard_url="http://127.0.0.1:1",
        hermes_executable="definitely-not-installed-hermes",
        ollama_base_url="http://127.0.0.1:1",
        runtime_probe_timeout_seconds=0.2,
        frontend_dist=tmp_path / "missing-dist",
    )


@pytest.fixture
def authenticated(client: TestClient):
    response = client.post(
        "/api/auth/login",
        headers={"Origin": ORIGIN},
        json={"password": PASSWORD},
    )
    assert response.status_code == 200
    csrf = response.json()["csrfToken"]
    return client, {"Origin": ORIGIN, "X-CSRF-Token": csrf}


@pytest.fixture
def teaching_app() -> dict:
    return {
        "hub": "teaching",
        "title": "Synthetic Workshop Planner",
        "description": "Synthetic data only.",
        "category": "Classroom tools",
        "tags": ["design", "year 9"],
        "icon": "app",
        "projectPath": "Apps/teaching/synthetic-workshop-planner",
        "launchUrl": "https://example.test/workshop",
        "developmentUrl": None,
        "integrationMode": "external_link",
        "authenticationRequirements": "Synthetic test only",
        "themeAdapterVersion": None,
        "aiAdapterSupport": False,
        "healthStatus": "unknown",
        "scriptId": None,
        "deploymentId": None
    }
