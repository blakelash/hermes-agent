"""Tests for the operator-Dashboard JSON-RPC methods (tui_gateway/server.py).

Covers the well-formed _ok envelopes the desktop Dashboard codes against:
- approvals.list — pending-approval snapshot for a session.
- approvals.respond — threads request_id through to resolve_gateway_approval.
- dashboard.snapshot — needs + specialists + cost + timestamp.
- files.tree / files.read — workspace listing + read, rooted at the session
  cwd with traversal rejected.

The handlers are invoked directly off the _methods registry; sessions are
seeded into _sessions the way the other gateway server tests do.
"""

from __future__ import annotations

import os

import pytest

import tui_gateway.server as srv
import tools.approval as approval


@pytest.fixture()
def session(tmp_path):
    """A live session rooted at a temp workspace, with no built agent."""
    sid = "dash-sid"
    sk = "dash-key"
    srv._sessions[sid] = {"session_key": sk, "agent": None, "cwd": str(tmp_path)}
    with approval._lock:
        approval._gateway_queues.pop(sk, None)
        approval._gateway_dashboard_cbs.pop(sk, None)
    yield {"sid": sid, "session_key": sk, "root": str(tmp_path), "tmp_path": tmp_path}
    srv._sessions.pop(sid, None)
    with approval._lock:
        approval._gateway_queues.pop(sk, None)
        approval._gateway_dashboard_cbs.pop(sk, None)


def _call(method: str, params: dict) -> dict:
    return srv._methods[method](1, params)


def _enqueue(session_key: str, data: dict):
    entry = approval._ApprovalEntry(data)
    data["request_id"] = entry.request_id
    with approval._lock:
        approval._gateway_queues.setdefault(session_key, []).append(entry)
    return entry


# ---------------------------------------------------------------------------
# session validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["approvals.list", "dashboard.snapshot", "files.tree", "files.read"])
def test_unknown_session_errors(method):
    env = _call(method, {"session_id": "nope"})
    assert env["error"]["code"] == 4001


# ---------------------------------------------------------------------------
# approvals.list
# ---------------------------------------------------------------------------


def test_approvals_list_empty(session):
    env = _call("approvals.list", {"session_id": session["sid"]})
    assert env["result"] == {"approvals": []}


def test_approvals_list_returns_pending_snapshot(session):
    _enqueue(session["session_key"], {"command": "rm -rf x", "description": "recursive delete"})
    env = _call("approvals.list", {"session_id": session["sid"]})
    approvals = env["result"]["approvals"]
    assert len(approvals) == 1
    assert approvals[0]["command"] == "rm -rf x"
    assert approvals[0]["description"] == "recursive delete"
    assert approvals[0]["request_id"]


# ---------------------------------------------------------------------------
# approvals.respond — request_id threading
# ---------------------------------------------------------------------------


def test_approvals_respond_by_request_id(session):
    e1 = _enqueue(session["session_key"], {"command": "a", "description": "d1"})
    e2 = _enqueue(session["session_key"], {"command": "b", "description": "d2"})

    env = _call(
        "approval.respond",
        {"session_id": session["sid"], "choice": "once", "request_id": e2.request_id},
    )
    assert env["result"] == {"resolved": 1}
    # Targeted the requested entry, left the head pending.
    assert e2.result == "once" and e2.event.is_set()
    assert e1.result is None and not e1.event.is_set()


def test_approvals_respond_fifo_without_request_id(session):
    e1 = _enqueue(session["session_key"], {"command": "a", "description": "d1"})
    e2 = _enqueue(session["session_key"], {"command": "b", "description": "d2"})

    env = _call("approval.respond", {"session_id": session["sid"], "choice": "deny"})
    assert env["result"] == {"resolved": 1}
    assert e1.event.is_set() and not e2.event.is_set()


# ---------------------------------------------------------------------------
# dashboard.snapshot
# ---------------------------------------------------------------------------


def test_dashboard_snapshot_shape(session, monkeypatch):
    _enqueue(session["session_key"], {"command": "mkfs", "description": "format"})
    monkeypatch.setattr(
        "tools.delegate_tool.list_active_subagents",
        lambda: [{"subagent_id": "sa-1", "goal": "research", "status": "running"}],
    )

    env = _call("dashboard.snapshot", {"session_id": session["sid"]})
    res = env["result"]

    assert {"needs", "specialists", "environments", "cost", "timestamp"} <= set(res)
    assert len(res["needs"]) == 1 and res["needs"][0]["description"] == "format"
    assert res["specialists"][0]["subagent_id"] == "sa-1"
    # environments is a real snapshot — always a list; with no live env it
    # carries the configured-backend row.
    assert isinstance(res["environments"], list)
    assert all({"id", "backend", "label", "status"} <= set(e) for e in res["environments"])
    # No built agent → zeroed cost block, still well-formed.
    assert res["cost"]["cost_usd"] == 0.0
    assert res["cost"]["total"] == 0
    assert isinstance(res["timestamp"], float)


def test_dashboard_snapshot_cost_from_agent(session, monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.list_active_subagents", lambda: [])
    monkeypatch.setattr(
        srv,
        "_get_usage",
        lambda agent: {
            "cost_usd": 1.25,
            "cost_status": "ok",
            "model": "claude-x",
            "input": 100,
            "output": 50,
            "cache_read": 10,
            "cache_write": 5,
            "total": 165,
            "calls": 3,
        },
    )
    srv._sessions[session["sid"]]["agent"] = object()

    env = _call("dashboard.snapshot", {"session_id": session["sid"]})
    cost = env["result"]["cost"]
    assert cost["cost_usd"] == 1.25
    assert cost["total"] == 165 and cost["calls"] == 3
    assert cost["model"] == "claude-x"


# ---------------------------------------------------------------------------
# files.tree
# ---------------------------------------------------------------------------


def test_files_tree_lists_workspace(session, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    root = session["tmp_path"]
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "sub").mkdir()

    env = _call("files.tree", {"session_id": session["sid"], "path": ""})
    res = env["result"]
    assert res["root"] == session["root"]
    by_name = {e["name"]: e for e in res["entries"]}
    assert by_name["a.txt"]["kind"] == "file" and by_name["a.txt"]["size"] == 5
    assert by_name["sub"]["kind"] == "dir"


def test_files_tree_subdir(session, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    root = session["tmp_path"]
    (root / "sub").mkdir()
    (root / "sub" / "nested.py").write_text("x=1", encoding="utf-8")

    env = _call("files.tree", {"session_id": session["sid"], "path": "sub"})
    res = env["result"]
    assert res["path"] == "sub"
    assert [e["name"] for e in res["entries"]] == ["nested.py"]


def test_files_tree_rejects_traversal(session, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    env = _call("files.tree", {"session_id": session["sid"], "path": "../../etc"})
    assert env["error"]["code"] == 4006


def test_files_tree_not_a_directory(session, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    (session["tmp_path"] / "f.txt").write_text("x", encoding="utf-8")
    env = _call("files.tree", {"session_id": session["sid"], "path": "f.txt"})
    assert env["error"]["code"] == 4007


# ---------------------------------------------------------------------------
# files.read
# ---------------------------------------------------------------------------


def test_files_read_returns_content(session, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    (session["tmp_path"] / "note.txt").write_text("line1\nline2\n", encoding="utf-8")

    env = _call("files.read", {"session_id": session["sid"], "path": "note.txt"})
    res = env["result"]
    assert "line1" in res["content"]
    assert res["total_lines"] == 2


def test_files_read_rejects_traversal(session, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    env = _call("files.read", {"session_id": session["sid"], "path": "/etc/passwd"})
    assert env["error"]["code"] == 4006


def test_files_read_missing_file(session, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    env = _call("files.read", {"session_id": session["sid"], "path": "ghost.txt"})
    assert env["error"]["code"] == 4007
