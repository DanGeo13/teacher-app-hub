from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from .database import Database
from .time import iso_now, utc_now


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    actor: str
    csrf_token: str | None
    expires_at: str


class AuthService:
    def __init__(self, database: Database, admin_password: str, ttl_seconds: int) -> None:
        self.database = database
        self._admin_password = admin_password
        self.ttl_seconds = ttl_seconds

    def login(self, password: str) -> tuple[str, str, str] | None:
        if not hmac.compare_digest(password.encode("utf-8"), self._admin_password.encode("utf-8")):
            return None
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = utc_now()
        expires = now + timedelta(seconds=self.ttl_seconds)
        with self.database.write() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso_now(),))
            connection.execute(
                """
                INSERT INTO sessions(
                    token_hash, csrf_token, actor, created_at, expires_at, last_seen_at
                ) VALUES (?, ?, 'admin', ?, ?, ?)
                """,
                (
                    _digest(token),
                    csrf,
                    now.isoformat(),
                    expires.isoformat(),
                    now.isoformat(),
                ),
            )
        return token, csrf, expires.isoformat()

    def authenticate(self, token: str | None, csrf: str | None = None) -> AuthenticatedSession | None:
        if not token:
            return None
        now = utc_now()
        with self.database.write() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE token_hash = ?", (_digest(token),)
            ).fetchone()
            if row is None or row["expires_at"] <= now.isoformat():
                if row is not None:
                    connection.execute(
                        "DELETE FROM sessions WHERE token_hash = ?", (_digest(token),)
                    )
                return None
            if csrf is not None and not hmac.compare_digest(row["csrf_token"], csrf):
                return None
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                (now.isoformat(), _digest(token)),
            )
            return AuthenticatedSession(
                actor=row["actor"],
                csrf_token=row["csrf_token"],
                expires_at=row["expires_at"],
            )

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.database.write() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))

    def csrf_for_session(self, token: str, csrf: str) -> bool:
        return self.authenticate(token, csrf) is not None
