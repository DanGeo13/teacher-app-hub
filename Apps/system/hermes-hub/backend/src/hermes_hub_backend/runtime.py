from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from .settings import Settings

RuntimeStatus = Literal["available", "unavailable", "authentication_required", "degraded"]
CapabilityState = Literal["verified", "unsupported", "unknown", "not_run"]


@dataclass(frozen=True, slots=True)
class RuntimeCapability:
    state: CapabilityState
    detail: str


@dataclass(frozen=True, slots=True)
class RuntimeProbe:
    component: str
    status: RuntimeStatus
    endpoint: str
    checked_at: str
    version: str | None
    detail: str
    capabilities: dict[str, RuntimeCapability]

    def to_dict(self) -> dict:
        value = asdict(self)
        value["checkedAt"] = value.pop("checked_at")
        return value


class HermesAdapter:
    """Version-aware availability adapter using documented, read-only interfaces."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def probe(self) -> RuntimeProbe:
        version = self._binary_version()
        endpoint = f"{self.settings.hermes_dashboard_url}/api/status"
        status, detail = _probe_json(endpoint, self.settings.runtime_probe_timeout_seconds)
        if status == "unavailable" and version:
            detail = f"Hermes CLI {version} is installed, but the dashboard endpoint is unavailable"
            status = "degraded"
        capabilities = {
            "sessions": RuntimeCapability(
                "unknown", "Reachability is not proof that authenticated session operations work"
            ),
            "streaming": RuntimeCapability(
                "not_run", "TUI Gateway JSON-RPC has not been exercised on this host"
            ),
            "tools": RuntimeCapability(
                "not_run", "No authentic Hermes tool call has been executed on this host"
            ),
            "approvals": RuntimeCapability(
                "not_run", "No Hermes approval round-trip has been executed on this host"
            ),
        }
        return RuntimeProbe(
            component="hermes",
            status=status,
            endpoint=self.settings.hermes_dashboard_url,
            checked_at=_now(),
            version=version,
            detail=detail,
            capabilities=capabilities,
        )

    def _binary_version(self) -> str | None:
        executable = shutil.which(self.settings.hermes_executable)
        if executable is None:
            return None
        try:
            result = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=self.settings.runtime_probe_timeout_seconds,
                check=False,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
        except (OSError, subprocess.SubprocessError):
            return None
        output = (result.stdout or result.stderr).strip().splitlines()
        return output[0][:160] if result.returncode == 0 and output else None


class OllamaAdapter:
    """Availability adapter for Ollama's documented version endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def probe(self) -> RuntimeProbe:
        endpoint = f"{self.settings.ollama_base_url}/api/version"
        status, detail, payload = _probe_json_payload(
            endpoint, self.settings.runtime_probe_timeout_seconds
        )
        version = payload.get("version") if isinstance(payload, dict) else None
        capabilities = {
            "text": RuntimeCapability(
                "not_run", "No text generation request has been executed on this host"
            ),
            "vision": RuntimeCapability(
                "not_run", "No image request has been executed on this host"
            ),
            "tools": RuntimeCapability(
                "not_run", "No tool-calling request has been executed on this host"
            ),
            "structuredOutput": RuntimeCapability(
                "not_run", "No schema-constrained response has been validated on this host"
            ),
        }
        return RuntimeProbe(
            component="qwen",
            status=status,
            endpoint=self.settings.ollama_base_url,
            checked_at=_now(),
            version=str(version)[:80] if version else None,
            detail=detail,
            capabilities=capabilities,
        )


def _probe_json(url: str, timeout: float) -> tuple[RuntimeStatus, str]:
    status, detail, _ = _probe_json_payload(url, timeout)
    return status, detail


def _probe_json_payload(url: str, timeout: float) -> tuple[RuntimeStatus, str, dict]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Hermes-Hub/0.1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(256_000)
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return "degraded", "Endpoint responded but did not return valid JSON", {}
            return "available", "Documented health endpoint responded", payload
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            return (
                "authentication_required",
                "Endpoint is reachable but requires authentication; authentication was not bypassed",
                {},
            )
        return "degraded", f"Endpoint returned HTTP {error.code}", {}
    except (urllib.error.URLError, TimeoutError, OSError):
        return "unavailable", "Runtime endpoint is not reachable from the Hub backend", {}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def probe_all(settings: Settings) -> dict:
    return {
        "hermes": HermesAdapter(settings).probe().to_dict(),
        "qwen": OllamaAdapter(settings).probe().to_dict(),
    }
