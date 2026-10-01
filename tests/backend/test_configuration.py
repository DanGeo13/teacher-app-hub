import json
from pathlib import Path

import pytest

from hermes_hub_backend.settings import ConfigurationError, Settings


def test_remote_runtime_endpoints_fail_closed(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        admin_password="synthetic-password-1234",
        allowed_origins=("http://testserver",),
        hermes_dashboard_url="https://runtime.example.test",
    )
    with pytest.raises(ConfigurationError, match="must be loopback"):
        settings.validate()


def test_short_password_rejected(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, admin_password="short")
    with pytest.raises(ConfigurationError, match="at least 16"):
        settings.validate()


def test_exact_origin_allow_list_is_required(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, admin_password="synthetic-password-1234")
    with pytest.raises(ConfigurationError, match="exact origin"):
        settings.validate()


def test_malformed_runtime_url_is_rejected(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        admin_password="synthetic-password-1234",
        allowed_origins=("http://testserver",),
        hermes_dashboard_url="http://[broken",
    )
    with pytest.raises(ConfigurationError, match="valid URL"):
        settings.validate()


def test_default_hermes_workspace_is_never_the_hub_data_directory(tmp_path: Path):
    """The ACP cwd is only a hint to the agent, not a sandbox boundary: it must
    never default to the directory holding hub.db and local backups.
    """
    settings = Settings(
        data_dir=tmp_path,
        admin_password="synthetic-password-1234",
        allowed_origins=("http://testserver",),
    )
    assert settings.hermes_workspace != settings.data_dir
    assert settings.hermes_workspace == settings.data_dir / "hermes-workspace"
    assert settings.hermes_workspace.is_relative_to(settings.data_dir)


def test_explicit_hermes_workspace_override_is_honoured(tmp_path: Path):
    override = tmp_path / "custom-hermes-workspace"
    settings = Settings(
        data_dir=tmp_path / "data",
        admin_password="synthetic-password-1234",
        allowed_origins=("http://testserver",),
        hermes_workspace_dir=override,
    )
    assert settings.hermes_workspace == override


def test_versioned_json_configuration_and_schemas_parse():
    root = Path(__file__).resolve().parents[2]
    paths = [*root.glob("config/**/*.json"), *root.glob("packages/**/*.json")]
    assert paths
    for path in paths:
        value = json.loads(path.read_text())
        assert value.get("$schema") or value.get("schemaVersion") == 1, path
