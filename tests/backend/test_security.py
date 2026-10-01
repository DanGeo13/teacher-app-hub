import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

from hermes_hub_backend.approvals import ApprovalService, canonical_content_hash
from hermes_hub_backend.errors import ApprovalError

from conftest import ORIGIN, PASSWORD


def test_authentication_and_csrf_are_enforced(client, teaching_app, settings):
    assert client.get("/api/apps").status_code == 401
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 403

    login = client.post(
        "/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
    )
    assert login.status_code == 200
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    raw_token = client.cookies.get(settings.cookie_name)
    connection = sqlite3.connect(settings.database_path)
    try:
        stored = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
    finally:
        connection.close()
    assert stored == hashlib.sha256(raw_token.encode()).hexdigest()
    assert raw_token not in settings.database_path.read_bytes().decode("utf-8", errors="ignore")
    assert settings.database_path.stat().st_mode & 0o777 == 0o600

    assert client.post("/api/apps", json=teaching_app).status_code == 403
    assert client.post(
        "/api/apps", headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"}, json=teaching_app
    ).status_code == 403
    assert client.post(
        "/api/apps",
        headers={"Origin": "https://evil.example", "X-CSRF-Token": login.json()["csrfToken"]},
        json=teaching_app,
    ).status_code == 403


def test_approval_invalidation_and_replay_rejection(settings):
    from hermes_hub_backend.database import Database

    service = ApprovalService(Database(settings.database_path))
    content = {"revision": "abc123", "files": ["README.md"]}
    pending = service.create("git_commit", "DanGeo13/teacher-app-hub", content, "admin")
    approved = service.approve(
        pending.id, pending.action, pending.target, pending.content_hash, "admin"
    )
    assert approved.status == "approved"
    consumed = service.consume(
        approved.id, approved.action, approved.target, content, actor="test-broker"
    )
    assert consumed.status == "consumed"
    try:
        service.consume(approved.id, approved.action, approved.target, content)
        raise AssertionError("approval replay was accepted")
    except ApprovalError as error:
        assert "consumed" in str(error)

    changed = service.create("git_push", "origin/branch", {"revision": "one"}, "admin")
    service.approve(changed.id, changed.action, changed.target, changed.content_hash, "admin")
    try:
        service.consume(changed.id, "git_push", "origin/other", {"revision": "two"})
        raise AssertionError("changed target/content was accepted")
    except ApprovalError as error:
        assert "invalidated" in str(error)
    assert next(row for row in service.list() if row.id == changed.id).status == "invalidated"


def test_approval_api_invalidates_changed_confirmation(authenticated):
    client, headers = authenticated
    requested = client.post(
        "/api/approvals",
        headers=headers,
        json={"action": "registry_export", "target": "config/apps.registry.json", "content": {"revision": 1}},
    )
    assert requested.status_code == 201
    record = requested.json()
    result = client.post(
        f"/api/approvals/{record['id']}/approve",
        headers=headers,
        json={
            "action": "registry_export",
            "target": "config/changed.json",
            "contentHash": record["contentHash"],
        },
    )
    assert result.status_code == 409
    assert client.get("/api/approvals").json()[0]["status"] == "invalidated"


def test_restricted_operations_fail_closed(authenticated):
    client, headers = authenticated
    response = client.post("/api/restricted-actions/git_push", headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "BROKER_NOT_IMPLEMENTED"


def test_private_api_responses_are_not_cacheable(authenticated):
    client, _headers = authenticated
    for path in ["/api/apps", "/api/runtime", "/api/approvals", "/api/audit"]:
        response = client.get(path)
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["pragma"] == "no-cache"


def test_secret_scanner_passes():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "scan-secrets.py")],
        cwd=root,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_content_hash_is_canonical():
    assert canonical_content_hash({"b": 2, "a": 1}) == canonical_content_hash({"a": 1, "b": 2})
