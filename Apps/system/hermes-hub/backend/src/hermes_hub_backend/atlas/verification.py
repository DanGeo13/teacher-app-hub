"""Live verification, started from the Hub's "Verify Atlas" button.

Runs on the host the Hub runs on (meant to be the Pixel 7 Pro). Each step is
PASS, FAIL or BLOCKED with a reason. The approval and digest dry runs use
synthetic data in a throwaway database; no real account is touched. A run on
any other machine is labelled as such and never counts as on-device proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from ..database import Database
from ..redaction import redact_text
from ..time import iso_now
from .host import AtlasHostSettings, SecretFileError, read_secret_file
from .service import AtlasError, AtlasService, resolve_agent_dir

PASS, FAIL, BLOCKED, RUNNING = "PASS", "FAIL", "BLOCKED", "RUNNING"
TELEGRAM_WAIT_SECONDS = 120


def host_identity() -> dict[str, Any]:
    android = bool(os.environ.get("TERMUX_VERSION") or os.environ.get("ANDROID_ROOT")
                   or "com.termux" in os.environ.get("PREFIX", ""))
    return {"platform": platform.platform(), "machine": platform.machine(), "android": android}


def _step(name: str, status: str, detail: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": redact_text(detail, max_length=600) or ""}


class VerificationRunner:
    def __init__(self, service: AtlasService, host: AtlasHostSettings, *,
                 http: Callable[..., dict[str, Any]] | None = None,
                 telegram_send: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
                 wait: Callable[[float], None] | None = None,
                 run_hermes_python: Callable[[list[str], dict[str, str]], subprocess.CompletedProcess] | None = None) -> None:
        self.service = service
        self.host = host
        self.http = http or self._http
        self.telegram_send = telegram_send
        self.wait = wait or (lambda seconds: threading.Event().wait(seconds))
        self.run_hermes_python = run_hermes_python or self._run_hermes_python
        self.agent_dir = resolve_agent_dir() if service.agent_dir is None else service.agent_dir

    # ---------------------------------------------------------------- utils

    @staticmethod
    def _http(method: str, url: str, headers: dict[str, str] | None = None, body: dict | None = None,
              timeout: float = 120.0) -> dict[str, Any]:
        request = urllib.request.Request(url, method=method, headers=headers or {},
                                         data=json.dumps(body).encode() if body is not None else None)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw.strip().startswith(("{", "[")) else {"raw": raw}

    def _run_hermes_python(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run([self.host.hermes_python or "", *args], capture_output=True, text=True,
                              timeout=300, env={**os.environ, **env})

    # ---------------------------------------------------------------- steps

    def step_hermes_responds(self) -> dict[str, Any]:
        name = "Hermes responds (Atlas API server)"
        if not self.host.api_server_key_file:
            return _step(name, BLOCKED, "ATLAS_API_SERVER_KEY_FILE is not set; the Atlas gateway API server is not configured")
        try:
            key = read_secret_file(self.host.api_server_key_file)
        except SecretFileError as error:
            return _step(name, BLOCKED, str(error))
        try:
            self.http("GET", f"{self.host.api_server_url}/health", timeout=10)
            reply = self.http(
                "POST", f"{self.host.api_server_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                body={"model": "atlas", "messages": [{"role": "user", "content":
                      "Atlas live verification (synthetic; take no action, call no tools). Reply with the single word: ready"}]},
            )
        except (urllib.error.URLError, OSError, ValueError) as error:
            return _step(name, FAIL, f"Atlas gateway did not answer at {self.host.api_server_url}: {error}")
        content = (((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            return _step(name, FAIL, "the API server answered without any assistant text")
        return _step(name, PASS, f"replied: {content[:80]}")

    def step_persona_loaded(self) -> dict[str, Any]:
        name = "Persona loaded"
        soul = self.host.profile_home / "SOUL.md"
        if not self.host.profile_home.is_dir():
            return _step(name, BLOCKED, f"Atlas profile {self.host.profile_home} does not exist on this host")
        if not soul.is_file():
            return _step(name, FAIL, f"{soul} is missing")
        repo = (self.agent_dir / "persona" / "SOUL.md").read_bytes()
        if hashlib.sha256(soul.read_bytes()).digest() != hashlib.sha256(repo).digest():
            return _step(name, FAIL, "installed SOUL.md differs from agent/atlas/persona/SOUL.md")
        return _step(name, PASS, "installed SOUL.md matches the repository persona")

    def steps_policy_and_skills(self) -> list[dict[str, Any]]:
        skills = sorted(p.parent.name for p in (self.agent_dir / "skills").glob("*/SKILL.md"))
        if not self.host.hermes_python:
            reason = "ATLAS_HERMES_PYTHON is not set (needs the Hermes install's python)"
            return [_step("Startup policy check", BLOCKED, reason)] + [
                _step(f"Skill discoverable: {s}", BLOCKED, reason) for s in skills]
        env = {"HERMES_HOME": str(self.host.profile_home), "ATLAS_AGENT_DIR": str(self.agent_dir)}
        policy = self.run_hermes_python(
            [str(self.agent_dir / "install" / "check_policy.py"), "--hermes-home", str(self.host.profile_home), "--json"], env)
        steps = []
        try:
            report = json.loads(policy.stdout)
            failed = [r for r in report["results"] if r["status"] != PASS]
            steps.append(_step("Startup policy check", PASS if report["ok"] and not failed else FAIL,
                               "; ".join(f"{r['check']}: {r['status']} {r['detail']}" for r in failed)))
        except (json.JSONDecodeError, KeyError):
            steps.append(_step("Startup policy check", FAIL, policy.stderr[-400:] or "no report"))
        listing = self.run_hermes_python(
            ["-c", "import json; from tools.skills_tool import skills_list; "
                   "print(json.dumps([s['name'] for s in json.loads(skills_list()).get('skills', [])]))"], env)
        try:
            found = set(json.loads(listing.stdout.strip().splitlines()[-1]))
        except (json.JSONDecodeError, IndexError):
            return steps + [_step(f"Skill discoverable: {s}", FAIL, listing.stderr[-300:]) for s in skills]
        return steps + [_step(f"Skill discoverable: {s}", PASS if s in found else FAIL,
                              "" if s in found else "Hermes skills_list did not return it") for s in skills]

    def step_database_writable(self) -> dict[str, Any]:
        name = "Hub database writable"
        try:
            with self.service.database.write() as connection:
                self.service._audit(connection, "atlas.verification.db_write", "verification", "db", {},
                                    actor="verification")
        except Exception as error:  # reported as FAIL
            return _step(name, FAIL, str(error))
        return _step(name, PASS, "wrote an audit entry")

    def step_telegram_round_trip(self, run_id: str) -> dict[str, Any]:
        name = "Telegram round trip"
        host = self.host
        if not (host.telegram_token_file and host.telegram_dan_user_id and host.telegram_chat_id):
            return _step(name, BLOCKED, "approval bot not configured (ATLAS_TELEGRAM_APPROVAL_TOKEN_FILE, "
                                        "ATLAS_TELEGRAM_DAN_USER_ID, ATLAS_TELEGRAM_CHAT_ID)")
        send = self.telegram_send
        if send is None:
            from .telegram_approvals import http_transport

            try:
                send = http_transport(read_secret_file(host.telegram_token_file))
            except SecretFileError as error:
                return _step(name, BLOCKED, str(error))
        try:
            send("sendMessage", {"chat_id": host.telegram_chat_id,
                                 "text": "Atlas live verification (synthetic). Tap Confirm within 2 minutes.",
                                 "reply_markup": {"inline_keyboard": [[{"text": "✅ Confirm",
                                                                        "callback_data": f"atl:v:{run_id}:x"}]]}})
        except RuntimeError as error:
            return _step(name, FAIL, f"could not send: {error}")
        waited = 0.0
        while waited < TELEGRAM_WAIT_SECONDS:
            with self.service.database.read() as connection:
                ack = connection.execute(
                    "SELECT 1 FROM audit_events WHERE event_type = 'atlas.verification.telegram_ack' AND target_id = ?",
                    (run_id,),
                ).fetchone()
            if ack:
                return _step(name, PASS, "message delivered and Dan's tap received by the approval bot")
            self.wait(2)
            waited += 2
        return _step(name, FAIL, "no tap received within 2 minutes (is the approval bot runner running?)")

    def step_approval_round_trip(self) -> dict[str, Any]:
        name = "Approval round trip (synthetic)"
        with tempfile.TemporaryDirectory(prefix="atlas-verify-") as tmp:
            svc = AtlasService(Database(Path(tmp) / "hub.db"), agent_dir=self.agent_dir)
            try:
                approval = svc.approvals.create("atlas_verification_noop", "atlas:verification", {"synthetic": True},
                                                "atlas-agent", store_payload=True, expires_in=timedelta(minutes=5))
                try:
                    svc.execute_approved_action(approval.id)
                    return _step(name, FAIL, "executed before approval")
                except AtlasError as error:
                    if error.code != "NOT_APPROVED":
                        return _step(name, FAIL, f"unexpected {error.code}")
                svc.approvals.approve(approval.id, approval.action, approval.target, approval.content_hash,
                                      "admin", "dashboard")
                first = svc.execute_approved_action(approval.id, actor="admin")
                replay = svc.execute_approved_action(approval.id, actor="admin")
                if not (first["executed"] and replay["idempotentReplay"]):
                    return _step(name, FAIL, "execution was not single-use/idempotent")
                if svc.approvals.get(approval.id).status != "consumed":
                    return _step(name, FAIL, "approval not consumed")
            except Exception as error:
                return _step(name, FAIL, str(error))
        return _step(name, PASS, "pending → approved → executed once → replay returned stored result")

    def step_digest_dry_run(self) -> dict[str, Any]:
        name = "Weekly digest dry run (synthetic)"
        with tempfile.TemporaryDirectory(prefix="atlas-verify-") as tmp:
            svc = AtlasService(Database(Path(tmp) / "hub.db"), agent_dir=self.agent_dir)
            today = svc._today()
            svc.propose_bill(title="SYNTHETIC overdue bill", payee="Example Utility", due_date=(today - timedelta(days=2)).isoformat(),
                             amount=10, source_account="synthetic", source_type="manual")
            svc.propose_car_service(title="SYNTHETIC service", vehicle="Example Car", due_date=(today + timedelta(days=9)).isoformat(),
                                    source_account="synthetic", source_type="manual")
            digest = svc.build_digest("telegram")
        titles = [s["title"] for s in digest["sections"]]
        expected = ["🔴 Urgent/overdue", "🟠 Action required", "🟡 Upcoming", "🟢 Completed with evidence", "System health"]
        if titles != expected or "SYNTHETIC overdue bill" not in digest["text"]:
            return _step(name, FAIL, f"sections were {titles}")
        return _step(name, PASS, digest["text"][:500])

    # ------------------------------------------------------------------ run

    def run(self, run_id: str | None = None) -> dict[str, Any]:
        run_id = run_id or uuid.uuid4().hex[:12]
        started = iso_now()
        steps = [self.step_hermes_responds(), self.step_persona_loaded(), *self.steps_policy_and_skills(),
                 self.step_database_writable(), self.step_telegram_round_trip(run_id),
                 self.step_approval_round_trip(), self.step_digest_dry_run()]
        host = host_identity()
        counts = {s: sum(1 for step in steps if step["status"] == s) for s in (PASS, FAIL, BLOCKED)}
        operational = host["android"] and counts[FAIL] == 0 and counts[BLOCKED] == 0
        result = {"runId": run_id, "startedAt": started, "finishedAt": iso_now(), "host": host, "steps": steps,
                  "counts": counts, "atlasOperationalOnThisHost": operational,
                  "verdict": ("Atlas verified on this Android host." if operational else
                              "Not verified: see FAIL/BLOCKED steps." if host["android"] else
                              "Not on-device proof: this run was not on the Pixel host.")}
        with self.service.database.write() as connection:
            self.service._audit(connection, "atlas.verification.completed", "verification", run_id,
                                {"counts": counts, "android": host["android"], "operational": operational},
                                actor="verification")
        return result


class VerificationManager:
    """Runs one verification at a time in the background for the Hub button."""

    def __init__(self, factory: Callable[[], VerificationRunner]) -> None:
        self.factory = factory
        self.latest: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.latest or {"status": RUNNING}
            run_id = uuid.uuid4().hex[:12]
            self.latest = {"runId": run_id, "status": RUNNING, "startedAt": iso_now(), "steps": []}

            def work() -> None:
                try:
                    result = self.factory().run(run_id)
                    result["status"] = "finished"
                except Exception as error:  # never silent
                    result = {"runId": run_id, "status": "error", "error": redact_text(str(error)), "steps": []}
                self.latest = result

            self._thread = threading.Thread(target=work, name="atlas-verification", daemon=True)
            self._thread.start()
            return self.latest
