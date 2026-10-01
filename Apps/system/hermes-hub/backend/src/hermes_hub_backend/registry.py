from __future__ import annotations

import json
import uuid
from typing import Any

from .audit import AuditService
from .database import Database
from .errors import ConflictError, NotFoundError
from .models import AppCreate, AppRecord, AppUpdate, ReorderRequest
from .time import iso_now


_FIELD_TO_COLUMN = {
    "title": "title",
    "description": "description",
    "category": "category",
    "tags": "tags_json",
    "icon": "icon",
    "project_path": "project_path",
    "launch_url": "launch_url",
    "development_url": "development_url",
    "integration_mode": "integration_mode",
    "authentication_requirements": "authentication_requirements",
    "theme_adapter_version": "theme_adapter_version",
    "ai_adapter_support": "ai_adapter_support",
    "health_status": "health_status",
    "script_id": "script_id",
    "deployment_id": "deployment_id",
    "archived": "archived",
}


class RegistryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _record(row) -> AppRecord:
        return AppRecord.model_validate(
            {
                "id": row["id"],
                "hub": row["hub"],
                "title": row["title"],
                "description": row["description"],
                "category": row["category"],
                "tags": json.loads(row["tags_json"]),
                "icon": row["icon"],
                "project_path": row["project_path"],
                "launch_url": row["launch_url"],
                "development_url": row["development_url"],
                "integration_mode": row["integration_mode"],
                "authentication_requirements": row["authentication_requirements"],
                "theme_adapter_version": row["theme_adapter_version"],
                "ai_adapter_support": bool(row["ai_adapter_support"]),
                "health_status": row["health_status"],
                "display_order": row["display_order"],
                "script_id": row["script_id"],
                "deployment_id": row["deployment_id"],
                "archived": bool(row["archived"]),
                "revision": row["revision"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    def list(
        self, hub: str | None = None, include_archived: bool = False, search: str = ""
    ) -> list[AppRecord]:
        clauses: list[str] = []
        values: list[Any] = []
        if hub:
            clauses.append("hub = ?")
            values.append(hub)
        if not include_archived:
            clauses.append("archived = 0")
        if search:
            clauses.append(
                "(lower(title) LIKE ? OR lower(description) LIKE ? OR lower(category) LIKE ? "
                "OR lower(tags_json) LIKE ?)"
            )
            needle = f"%{search.lower()}%"
            values.extend([needle, needle, needle, needle])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM apps {where} ORDER BY hub, archived, display_order, title",
                values,
            ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, app_id: str) -> AppRecord:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        if row is None:
            raise NotFoundError("application not found")
        return self._record(row)

    def create(self, value: AppCreate, actor: str) -> AppRecord:
        app_id = str(uuid.uuid4())
        now = iso_now()
        data = value.model_dump()
        with self.database.write() as connection:
            display_order = int(
                connection.execute(
                    "SELECT COALESCE(MAX(display_order), -1) + 1 FROM apps WHERE hub = ?",
                    (value.hub,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO apps(
                    id, hub, title, description, category, tags_json, icon, project_path,
                    launch_url, development_url, integration_mode,
                    authentication_requirements, theme_adapter_version, ai_adapter_support,
                    health_status, display_order, script_id, deployment_id, archived,
                    revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                """,
                (
                    app_id,
                    value.hub,
                    value.title,
                    value.description,
                    value.category,
                    json.dumps(value.tags, separators=(",", ":")),
                    value.icon,
                    value.project_path,
                    value.launch_url,
                    value.development_url,
                    value.integration_mode,
                    value.authentication_requirements,
                    value.theme_adapter_version,
                    int(value.ai_adapter_support),
                    value.health_status,
                    display_order,
                    value.script_id,
                    value.deployment_id,
                    now,
                    now,
                ),
            )
            AuditService.record(
                connection,
                "registry.app_created",
                actor,
                "app",
                app_id,
                {"hub": value.hub, "title": value.title},
            )
            row = connection.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        return self._record(row)

    def update(self, app_id: str, value: AppUpdate, actor: str) -> AppRecord:
        changes = value.model_dump(exclude={"expected_revision"}, exclude_unset=True)
        if not changes:
            return self.get(app_id)
        assignments: list[str] = []
        values: list[Any] = []
        changed_fields: list[str] = []
        for field, raw_value in changes.items():
            column = _FIELD_TO_COLUMN[field]
            if field == "tags":
                raw_value = json.dumps(raw_value, separators=(",", ":"))
            elif field in {"ai_adapter_support", "archived"}:
                raw_value = int(raw_value)
            assignments.append(f"{column} = ?")
            values.append(raw_value)
            changed_fields.append(field)
        assignments.extend(["revision = revision + 1", "updated_at = ?"])
        values.append(iso_now())
        values.extend([app_id, value.expected_revision])

        with self.database.write() as connection:
            cursor = connection.execute(
                f"UPDATE apps SET {', '.join(assignments)} WHERE id = ? AND revision = ?",
                values,
            )
            if cursor.rowcount != 1:
                exists = connection.execute(
                    "SELECT revision FROM apps WHERE id = ?", (app_id,)
                ).fetchone()
                if exists is None:
                    raise NotFoundError("application not found")
                raise ConflictError(
                    f"application revision changed; current revision is {exists['revision']}"
                )
            AuditService.record(
                connection,
                "registry.app_updated",
                actor,
                "app",
                app_id,
                {"changedFields": sorted(changed_fields)},
            )
            row = connection.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        return self._record(row)

    def reorder(self, request: ReorderRequest, actor: str) -> list[AppRecord]:
        with self.database.write() as connection:
            rows = connection.execute(
                "SELECT id, revision FROM apps WHERE hub = ? AND archived = 0 ORDER BY display_order",
                (request.hub,),
            ).fetchall()
            actual_ids = {row["id"] for row in rows}
            if actual_ids != set(request.ordered_ids):
                raise ConflictError("the active application set changed; reload before reordering")
            actual_revisions = {row["id"]: row["revision"] for row in rows}
            if actual_revisions != request.expected_revisions:
                raise ConflictError("an application changed; reload before reordering")
            now = iso_now()
            for order, app_id in enumerate(request.ordered_ids):
                connection.execute(
                    """
                    UPDATE apps
                    SET display_order = ?, revision = revision + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (order, now, app_id),
                )
            AuditService.record(
                connection,
                "registry.apps_reordered",
                actor,
                "hub",
                request.hub,
                {"orderedIds": request.ordered_ids},
            )
            updated = connection.execute(
                "SELECT * FROM apps WHERE hub = ? AND archived = 0 ORDER BY display_order",
                (request.hub,),
            ).fetchall()
        return [self._record(row) for row in updated]

    def export(self) -> dict[str, Any]:
        apps = self.list(include_archived=True)
        return {
            "schemaVersion": 1,
            "apps": [app.model_dump(mode="json", by_alias=True) for app in apps],
        }
