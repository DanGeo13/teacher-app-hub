from fastapi.testclient import TestClient

from hermes_hub_backend.api import create_app

from conftest import ORIGIN, PASSWORD


def create(client, headers, payload):
    response = client.post("/api/apps", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_registry_create_edit_reorder_archive_and_categories(authenticated, teaching_app):
    client, headers = authenticated
    first = create(client, headers, teaching_app)
    second_payload = {**teaching_app, "title": "Materials Library", "category": "Resources"}
    second = create(client, headers, second_payload)
    personal_payload = {
        **teaching_app,
        "hub": "personal",
        "title": "Synthetic Meal Planner",
        "category": "Home",
        "projectPath": "Apps/personal/synthetic-meal-planner",
        "launchUrl": "https://example.test/meals",
    }
    personal = create(client, headers, personal_payload)

    edited = client.patch(
        f"/api/apps/{first['id']}",
        headers=headers,
        json={"expectedRevision": first["revision"], "category": "TAS planning"},
    )
    assert edited.status_code == 200
    assert edited.json()["category"] == "TAS planning"

    listed = client.get("/api/apps?hub=teaching&include_archived=true").json()
    assert {entry["hub"] for entry in listed} == {"teaching"}
    assert client.get("/api/apps?hub=personal").json()[0]["id"] == personal["id"]

    current = client.get("/api/apps?hub=teaching").json()
    reordered = list(reversed(current))
    response = client.post(
        "/api/apps/reorder",
        headers=headers,
        json={
            "hub": "teaching",
            "orderedIds": [entry["id"] for entry in reordered],
            "expectedRevisions": {entry["id"]: entry["revision"] for entry in reordered},
        },
    )
    assert response.status_code == 200
    assert response.json()[0]["id"] == second["id"]

    newest_first = next(entry for entry in response.json() if entry["id"] == first["id"])
    archived = client.patch(
        f"/api/apps/{first['id']}",
        headers=headers,
        json={"expectedRevision": newest_first["revision"], "archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert all(entry["id"] != first["id"] for entry in client.get("/api/apps?hub=teaching").json())

    audit_types = {event["eventType"] for event in client.get("/api/audit").json()}
    assert {"registry.app_created", "registry.app_updated", "registry.apps_reordered"} <= audit_types


def test_registry_persists_after_backend_restart(settings, teaching_app):
    with TestClient(create_app(settings)) as first_client:
        login = first_client.post(
            "/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrfToken"]}
        created = create(first_client, headers, teaching_app)
        session_cookie = first_client.cookies.get(settings.cookie_name)

    with TestClient(create_app(settings)) as restarted_client:
        restarted_client.cookies.set(settings.cookie_name, session_cookie)
        session = restarted_client.get("/api/auth/session")
        assert session.json()["authenticated"] is True
        apps = restarted_client.get("/api/apps?hub=teaching").json()
        assert [entry["id"] for entry in apps] == [created["id"]]


def test_revision_conflict(authenticated, teaching_app):
    client, headers = authenticated
    app = create(client, headers, teaching_app)
    first_update = client.patch(
        f"/api/apps/{app['id']}",
        headers=headers,
        json={"expectedRevision": 1, "title": "Updated once"},
    )
    assert first_update.status_code == 200
    stale_update = client.patch(
        f"/api/apps/{app['id']}",
        headers=headers,
        json={"expectedRevision": 1, "title": "Stale edit"},
    )
    assert stale_update.status_code == 409
    assert stale_update.json()["error"]["code"] == "REVISION_CONFLICT"


def test_invalid_and_unsafe_urls_are_rejected(authenticated, teaching_app):
    client, headers = authenticated
    for unsafe in [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://example.test/insecure",
        "https://user:password@example.test/private",
    ]:
        response = client.post(
            "/api/apps", headers=headers, json={**teaching_app, "launchUrl": unsafe}
        )
        assert response.status_code == 422, unsafe

    traversal = client.post(
        "/api/apps",
        headers=headers,
        json={**teaching_app, "projectPath": "../private"},
    )
    assert traversal.status_code == 422
