"""Atlas host configuration (Pixel 7 host). Secrets come only from files.

Every secret is read from a file path given in the environment. The file must
be a regular file owned by the Hub's user with no group/other permissions
(0600). Values are never logged or returned by any API.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class SecretFileError(RuntimeError):
    pass


def read_secret_file(path: str | os.PathLike[str]) -> str:
    target = Path(path).expanduser()
    try:
        info = target.lstat()
    except FileNotFoundError as error:
        raise SecretFileError(f"secret file {target} does not exist") from error
    if not stat.S_ISREG(info.st_mode):
        raise SecretFileError(f"secret file {target} is not a regular file")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise SecretFileError(f"secret file {target} is not owned by the Hub user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SecretFileError(f"secret file {target} must be mode 0600")
    value = target.read_text(encoding="utf-8").strip()
    if not value:
        raise SecretFileError(f"secret file {target} is empty")
    return value


def _int_env(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    return int(value) if value else None


@dataclass(frozen=True, slots=True)
class AtlasHostSettings:
    hermes_python: str | None
    hermes_executable: str | None
    hermes_root: Path
    api_server_url: str
    api_server_key_file: str | None
    telegram_token_file: str | None
    telegram_dan_user_id: int | None
    telegram_chat_id: int | None

    @property
    def profile_home(self) -> Path:
        return self.hermes_root / "profiles" / "atlas"

    @classmethod
    def from_env(cls) -> "AtlasHostSettings":
        url = os.environ.get("ATLAS_API_SERVER_URL", "http://127.0.0.1:8642").rstrip("/")
        if not url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            raise ValueError("ATLAS_API_SERVER_URL must be a loopback URL")
        return cls(
            hermes_python=os.environ.get("ATLAS_HERMES_PYTHON") or None,
            hermes_executable=os.environ.get("HERMES_EXECUTABLE") or None,
            hermes_root=Path(os.environ.get("ATLAS_HERMES_ROOT", "~/.hermes")).expanduser(),
            api_server_url=url,
            api_server_key_file=os.environ.get("ATLAS_API_SERVER_KEY_FILE") or None,
            telegram_token_file=os.environ.get("ATLAS_TELEGRAM_APPROVAL_TOKEN_FILE") or None,
            telegram_dan_user_id=_int_env("ATLAS_TELEGRAM_DAN_USER_ID"),
            telegram_chat_id=_int_env("ATLAS_TELEGRAM_CHAT_ID"),
        )
