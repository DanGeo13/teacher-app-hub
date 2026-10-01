from __future__ import annotations

import posixpath
import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


HubName = Literal["teaching", "personal"]
IntegrationMode = Literal["external_link", "embedded_legacy", "hub_aware"]
HealthStatus = Literal["unknown", "healthy", "degraded", "unavailable"]
ApprovalAction = Literal[
    "registry_export",
    "git_commit",
    "git_push",
    "clasp_push",
    "apps_script_deploy",
    "external_backup",
    "restore",
    "destructive_change",
]


def _safe_url(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if len(value) > 2048:
        raise ValueError("URL must be 2048 characters or fewer")
    if any(ord(char) < 0x20 or char == "\\" for char in value):
        raise ValueError("URL must not contain control characters or backslashes")
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as error:
        raise ValueError("URL contains an invalid host or port") from error
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("URL must use http or https and include a host")
    if parsed.username or parsed.password:
        raise ValueError("URL must not contain embedded credentials")
    if parsed.scheme == "http" and hostname.lower() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("non-loopback application URLs must use https")
    return value


def _required_safe_url(value: str) -> str:
    safe = _safe_url(value)
    if safe is None:
        raise ValueError("URL is required")
    return safe


def _project_path(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    value = value.replace("\\", "/")
    normalised = posixpath.normpath(value)
    if (
        value.startswith("/")
        or normalised.startswith("../")
        or normalised == ".."
        or not normalised.startswith("Apps/")
    ):
        raise ValueError("projectPath must be a repository-relative path under Apps/")
    return normalised


def _tags(value: list[str]) -> list[str]:
    normalised: list[str] = []
    for tag in value:
        clean = tag.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9 _-]{0,31}", clean):
            raise ValueError("tags must contain only letters, numbers, spaces, hyphens or underscores")
        if clean not in normalised:
            normalised.append(clean)
    if len(normalised) > 20:
        raise ValueError("a maximum of 20 tags is allowed")
    return normalised


class AppCreate(ApiModel):
    hub: HubName
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    category: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list)
    icon: str = Field(default="app", min_length=1, max_length=64)
    project_path: str | None = Field(default=None, max_length=300)
    launch_url: str
    development_url: str | None = None
    integration_mode: IntegrationMode = "external_link"
    authentication_requirements: str = Field(default="", max_length=500)
    theme_adapter_version: str | None = Field(default=None, max_length=40)
    ai_adapter_support: bool = False
    health_status: HealthStatus = "unknown"
    script_id: str | None = Field(default=None, max_length=160)
    deployment_id: str | None = Field(default=None, max_length=220)

    _validate_launch_url = field_validator("launch_url")(_required_safe_url)
    _validate_development_url = field_validator("development_url")(_safe_url)
    _validate_project_path = field_validator("project_path")(_project_path)
    _validate_tags = field_validator("tags")(_tags)


class AppUpdate(ApiModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    tags: list[str] | None = None
    icon: str | None = Field(default=None, min_length=1, max_length=64)
    project_path: str | None = Field(default=None, max_length=300)
    launch_url: str | None = None
    development_url: str | None = None
    integration_mode: IntegrationMode | None = None
    authentication_requirements: str | None = Field(default=None, max_length=500)
    theme_adapter_version: str | None = Field(default=None, max_length=40)
    ai_adapter_support: bool | None = None
    health_status: HealthStatus | None = None
    script_id: str | None = Field(default=None, max_length=160)
    deployment_id: str | None = Field(default=None, max_length=220)
    archived: bool | None = None

    _validate_launch_url = field_validator("launch_url")(_safe_url)
    _validate_development_url = field_validator("development_url")(_safe_url)
    _validate_project_path = field_validator("project_path")(_project_path)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _tags(value)

    @model_validator(mode="after")
    def launch_url_cannot_be_cleared(self) -> "AppUpdate":
        if "launch_url" in self.model_fields_set and self.launch_url is None:
            raise ValueError("launchUrl cannot be null or empty")
        return self


class AppRecord(AppCreate):
    id: str
    display_order: int
    archived: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class ReorderRequest(ApiModel):
    hub: HubName
    ordered_ids: list[str] = Field(min_length=1, max_length=500)
    expected_revisions: dict[str, int]

    @model_validator(mode="after")
    def ids_match_revisions(self) -> "ReorderRequest":
        if len(set(self.ordered_ids)) != len(self.ordered_ids):
            raise ValueError("orderedIds must not contain duplicates")
        if set(self.ordered_ids) != set(self.expected_revisions):
            raise ValueError("expectedRevisions must contain exactly the orderedIds")
        return self


class LoginRequest(ApiModel):
    password: str = Field(min_length=1, max_length=1024)


class SessionResponse(ApiModel):
    authenticated: bool
    actor: str | None = None
    csrf_token: str | None = None
    expires_at: datetime | None = None


class ApprovalCreate(ApiModel):
    action: ApprovalAction
    target: str = Field(min_length=1, max_length=512)
    content: Any


class ApprovalConfirm(ApiModel):
    action: ApprovalAction
    target: str = Field(min_length=1, max_length=512)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ApprovalRecord(ApiModel):
    id: str
    action: str
    target: str
    content_hash: str
    status: str
    requested_by: str
    approved_by: str | None
    created_at: datetime
    approved_at: datetime | None
    consumed_at: datetime | None
    invalidated_at: datetime | None
    invalidation_reason: str | None


class BackupRecord(ApiModel):
    id: str
    filename: str
    sha256: str
    schema_version: int
    size_bytes: int
    created_at: datetime
    status: str
