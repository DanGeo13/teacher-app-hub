from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigurationError(RuntimeError):
    """Raised when a security-sensitive setting is missing or unsafe."""


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _validate_runtime_url(name: str, value: str, allow_remote: bool) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(f"{name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} must not contain credentials, query parameters or fragments")
    loopback = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    if not allow_remote and not loopback:
        raise ConfigurationError(
            f"{name} must be loopback unless HUB_ALLOW_REMOTE_RUNTIME_ENDPOINTS=true"
        )
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    admin_password: str
    cookie_secure: bool = True
    allowed_origins: tuple[str, ...] = ()
    session_ttl_seconds: int = 43_200
    hermes_dashboard_url: str = "http://127.0.0.1:9119"
    hermes_executable: str = "hermes"
    ollama_base_url: str = "http://127.0.0.1:11434"
    runtime_probe_timeout_seconds: float = 2.0
    frontend_dist: Path | None = None
    allow_remote_runtime_endpoints: bool = False
    allow_short_test_password: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        password = os.getenv("HUB_ADMIN_PASSWORD", "")
        allow_short = _bool_env("HUB_ALLOW_SHORT_TEST_PASSWORD", False)
        if not password:
            raise ConfigurationError("HUB_ADMIN_PASSWORD is required")
        if len(password) < 16 and not allow_short:
            raise ConfigurationError("HUB_ADMIN_PASSWORD must contain at least 16 characters")

        data_value = os.getenv("HUB_DATA_DIR")
        if not data_value:
            raise ConfigurationError(
                "HUB_DATA_DIR is required; use a writable path outside the tracked repository"
            )
        data_dir = Path(data_value).expanduser().resolve()

        allow_remote = _bool_env("HUB_ALLOW_REMOTE_RUNTIME_ENDPOINTS", False)
        origins = tuple(
            origin.strip().rstrip("/")
            for origin in os.getenv("HUB_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        )
        frontend_value = os.getenv("HUB_FRONTEND_DIST")
        if frontend_value:
            frontend_dist = Path(frontend_value).expanduser().resolve()
        else:
            frontend_dist = (
                Path(__file__).resolve().parents[3] / "frontend" / "dist"
            )

        settings = cls(
            data_dir=data_dir,
            admin_password=password,
            cookie_secure=_bool_env("HUB_COOKIE_SECURE", True),
            allowed_origins=origins,
            session_ttl_seconds=int(os.getenv("HUB_SESSION_TTL_SECONDS", "43200")),
            hermes_dashboard_url=os.getenv(
                "HERMES_DASHBOARD_URL", "http://127.0.0.1:9119"
            ),
            hermes_executable=os.getenv("HERMES_EXECUTABLE", "hermes"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            runtime_probe_timeout_seconds=float(
                os.getenv("HUB_RUNTIME_PROBE_TIMEOUT_SECONDS", "2")
            ),
            frontend_dist=frontend_dist,
            allow_remote_runtime_endpoints=allow_remote,
            allow_short_test_password=allow_short,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if len(self.admin_password) < 16 and not self.allow_short_test_password:
            raise ConfigurationError("admin password must contain at least 16 characters")
        if self.session_ttl_seconds < 300 or self.session_ttl_seconds > 86_400:
            raise ConfigurationError("session TTL must be between 300 and 86400 seconds")
        if not 0.2 <= self.runtime_probe_timeout_seconds <= 10:
            raise ConfigurationError("runtime probe timeout must be between 0.2 and 10 seconds")
        _validate_runtime_url(
            "HERMES_DASHBOARD_URL",
            self.hermes_dashboard_url,
            self.allow_remote_runtime_endpoints,
        )
        _validate_runtime_url(
            "OLLAMA_BASE_URL", self.ollama_base_url, self.allow_remote_runtime_endpoints
        )
        for origin in self.allowed_origins:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigurationError(f"invalid HUB_ALLOWED_ORIGINS value: {origin}")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "hub.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def cookie_name(self) -> str:
        return "__Host-hermes_hub_session" if self.cookie_secure else "hermes_hub_session"
