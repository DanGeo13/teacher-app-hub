"""Step 3: the atlas-hub MCP server over real stdio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import atlas_policy
from atlas_helpers import AGENT_DIR
from hermes_hub_backend.database import Database


class StdioClient:
    def __init__(self, data_dir: Path) -> None:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(data_dir), "HUB_DATA_DIR": str(data_dir),
               "ATLAS_AGENT_DIR": str(AGENT_DIR)}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "atlas_hub_mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env,
        )
        self.next_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})["result"]

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


@pytest.fixture
def client(tmp_path):
    Database(tmp_path / "hub.db")  # the Hub migrates; the MCP server never does
    c = StdioClient(tmp_path)
    init = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    c.notify("notifications/initialized")
    yield c
    c.close()


def test_tool_list_contains_no_approval_tool(client):
    tools = client.request("tools/list")["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == set(atlas_policy.model_facing_atlas_tools())
    forbidden = set(atlas_policy.load_policy()["atlasHubTools"]["forbidden"])
    assert not names & forbidden
    assert not any(n in names for n in ("approve_action", "deny_action", "decide_approval", "execute_sql", "delete_record"))
    for tool in tools:
        assert tool["inputSchema"]["additionalProperties"] is False


def test_forbidden_tool_call_is_refused_with_structured_error(client):
    for name in ("approve_action", "execute_sql", "delete_record", "merge_records"):
        result = client.call(name, {"approval_id": "x"})
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"] == "UNKNOWN_TOOL"


def test_end_to_end_bill_flow_over_stdio(client, tmp_path):
    created = client.call("propose_bill", {
        "title": "Water", "payee": "Sydney Water", "due_date": "30/10/2026", "amount": 88.1,
        "source_account": "gmail_personal", "source_type": "email"})
    assert created["isError"] is False
    record = created["structuredContent"]["record"]
    assert record["label"] == "proposed" and record["dueDate"] == "30/10/2026"

    bad = client.call("propose_status_change", {"record_id": record["id"], "to_status": "confirmed_paid"})
    assert bad["isError"] is True
    assert bad["structuredContent"]["error"]["code"] == "EVIDENCE_REQUIRED"

    unknown_arg = client.call("get_record", {"record_id": record["id"], "sql": "DROP TABLE approvals"})
    assert unknown_arg["structuredContent"]["error"]["code"] == "INVALID_ARGUMENTS"

    pending = client.call("execute_approved_action", {"approval_id": "does-not-exist"})
    assert pending["structuredContent"]["error"]["code"] == "NOT_FOUND"


def test_server_refuses_to_start_without_migrated_database(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "HUB_DATA_DIR": str(tmp_path)}
    proc = subprocess.run([sys.executable, "-m", "atlas_hub_mcp"], input="", capture_output=True, text=True,
                          env=env, timeout=20)
    assert proc.returncode != 0
    assert "does not exist" in proc.stderr
    assert not (tmp_path / "hub.db").exists(), "the MCP server must never create or migrate the Hub database"


def test_unknown_method_and_bad_json(client):
    assert client.request("resources/list")["error"]["code"] == -32601
    client.proc.stdin.write("{not json\n")
    client.proc.stdin.flush()
    assert json.loads(client.proc.stdout.readline())["error"]["code"] == -32700
