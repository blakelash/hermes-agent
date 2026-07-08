"""Tests for the agent's project_bind tool (service-gated).

The tool must be invisible outside a gateway (its check_fn keys off the
service registration), never reset the conversation (mid-turn binds skip
agent eviction), and route through the same binding helpers as /project.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import tools.project_bind_tool as pbt
import tools.projects as projects
import tools.terminal_tool as tt
from gateway.session_context import clear_session_vars, set_session_vars

SESSION_KEY = "telegram:12345:67890"
SESSION_ID = "sess_123"


@pytest.fixture(autouse=True)
def _clean_service():
    yield
    pbt.clear_project_binding_service()
    tt.clear_task_env_overrides(SESSION_ID)


@pytest.fixture()
def session_ctx():
    tokens = set_session_vars(platform="telegram", session_key=SESSION_KEY)
    yield
    clear_session_vars(tokens)


# ──────────────────────────────────────────────────────────────────────────
# Gating
# ──────────────────────────────────────────────────────────────────────────

def test_hidden_without_service():
    assert pbt.check_project_bind_requirements() is False
    out = json.loads(pbt.project_bind_tool("list"))
    assert "error" in out


def test_visible_with_service():
    pbt.register_project_binding_service({"list": lambda: [], "status": None, "bind": None})
    assert pbt.check_project_bind_requirements() is True


def test_registered_in_core_toolset():
    from toolsets import _HERMES_CORE_TOOLS

    assert "project_bind" in _HERMES_CORE_TOOLS
    from tools.registry import registry

    entry = registry.get_entry("project_bind")
    assert entry is not None and entry.check_fn is pbt.check_project_bind_requirements


def test_requires_session_context():
    pbt.register_project_binding_service({"list": lambda: []})
    out = json.loads(pbt.project_bind_tool("status"))
    assert "error" in out and "session" in out["error"].lower()


# ──────────────────────────────────────────────────────────────────────────
# Dispatch through a fake service
# ──────────────────────────────────────────────────────────────────────────

def test_actions_dispatch(session_ctx):
    service = {
        "status": MagicMock(return_value={"bound": False}),
        "list": MagicMock(return_value=[{"slug": "rna", "name": "RNA"}]),
        "bind": MagicMock(return_value={"bound": True, "workdir": "/work/rna/s1"}),
    }
    pbt.register_project_binding_service(service)

    assert json.loads(pbt.project_bind_tool("status")) == {"bound": False}
    service["status"].assert_called_with(SESSION_KEY)

    assert json.loads(pbt.project_bind_tool("list"))["projects"][0]["slug"] == "rna"

    out = json.loads(pbt.project_bind_tool("bind", "rna"))
    assert out["workdir"] == "/work/rna/s1"
    service["bind"].assert_called_with(SESSION_KEY, "rna", create=False)

    pbt.project_bind_tool("create", "New Thing")
    service["bind"].assert_called_with(SESSION_KEY, "New Thing", create=True)


def test_bind_requires_name(session_ctx):
    pbt.register_project_binding_service({"bind": MagicMock()})
    out = json.loads(pbt.project_bind_tool("bind"))
    assert "error" in out


def test_value_error_surfaces_as_tool_error(session_ctx):
    def _bind(*a, **k):
        raise ValueError('no project matches "x"')

    pbt.register_project_binding_service({"bind": _bind})
    out = json.loads(pbt.project_bind_tool("bind", "x"))
    assert "no project matches" in out["error"]


def test_unknown_action(session_ctx):
    pbt.register_project_binding_service({})
    out = json.loads(pbt.project_bind_tool("frobnicate"))
    assert "error" in out


# ──────────────────────────────────────────────────────────────────────────
# Real gateway service (built from the runner mixin)
# ──────────────────────────────────────────────────────────────────────────

def _make_runner(tmp_path):
    from gateway.run import GatewayRunner
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    runner = object.__new__(GatewayRunner)
    runner._session_db = db

    entry = MagicMock()
    entry.session_id = SESSION_ID
    entry.session_key = SESSION_KEY
    entry.project_slug = ""
    store = MagicMock()
    store.get_session_by_key.return_value = entry

    def _set_project(session_key, slug):
        entry.project_slug = slug
        return entry

    store.set_session_project.side_effect = _set_project
    runner.session_store = store
    runner._evict_cached_agent = MagicMock()
    return runner, db, entry


def test_real_service_bind_no_reset_no_eviction(tmp_path, session_ctx):
    runner, db, entry = _make_runner(tmp_path)
    p = projects.create_project("RNA Analysis")
    db.create_session(SESSION_ID, "telegram")
    pbt.register_project_binding_service(runner._build_project_binding_service())

    out = json.loads(pbt.project_bind_tool("bind", "rna anal"))

    assert out["bound"] is True and out["project"] == "rna-analysis"
    assert out["workdir"].startswith(p["cwd"])
    # Immediate effect: entry bound, DB row stamped, sticky default set,
    # terminal override pinned — and the running agent left alone.
    assert entry.project_slug == "rna-analysis"
    assert db.get_session(SESSION_ID)["project"] == "rna-analysis"
    assert db.get_chat_project_default(SESSION_KEY) == "rna-analysis"
    assert tt.resolve_task_overrides(SESSION_ID).get("pin_cwd") is True
    runner._evict_cached_agent.assert_not_called()
    db.close()


def test_real_service_create_and_status(tmp_path, session_ctx):
    runner, db, entry = _make_runner(tmp_path)
    db.create_session(SESSION_ID, "telegram")
    pbt.register_project_binding_service(runner._build_project_binding_service())

    out = json.loads(pbt.project_bind_tool("create", "Fresh Effort"))
    assert out["created"] is True and out["project"] == "fresh-effort"
    assert out["sticky"] is True

    status = json.loads(pbt.project_bind_tool("status"))
    assert status["bound"] is True and status["project"] == "fresh-effort"
    db.close()


def test_bind_reports_degraded_stickiness_when_db_broken(tmp_path, session_ctx):
    """The immediate bind still works, but the tool must not promise sticky
    behavior the durability layer can't deliver."""
    runner, db, entry = _make_runner(tmp_path)
    projects.create_project("RNA")
    db.create_session(SESSION_ID, "telegram")

    def _boom(*a, **k):
        raise RuntimeError("db down")

    runner._session_db.set_chat_project_default = _boom
    pbt.register_project_binding_service(runner._build_project_binding_service())

    out = json.loads(pbt.project_bind_tool("bind", "rna"))

    assert out["bound"] is True  # immediate bind still applied
    assert entry.project_slug == "rna"
    assert out["sticky"] is False
    assert "could not be persisted" in out["note"]
    db.close()
