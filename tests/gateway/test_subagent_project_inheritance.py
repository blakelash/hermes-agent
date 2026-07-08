"""Tests for delegated-child / kanban project-workdir inheritance.

Children of a project-bound (pinned) session work in their own subdirectory
of the parent's session dir — parallel children share one sandbox, so path
nesting IS the isolation. Kanban tasks created from a pinned local-backend
session land in a project subdir instead of a scratch dir.
"""

from __future__ import annotations

import json
import os

import pytest

import tools.terminal_tool as tt

PARENT = "parent-sess-1"
CHILD = "sa-1-abc123"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    tt.clear_task_env_overrides(PARENT)
    tt.clear_task_env_overrides(CHILD)


# ──────────────────────────────────────────────────────────────────────────
# inherit_pinned_cwd (the mechanism delegate_task uses)
# ──────────────────────────────────────────────────────────────────────────

def test_local_child_gets_own_subdir(tmp_path, monkeypatch):
    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    parent_dir = tmp_path / "proj" / PARENT
    parent_dir.mkdir(parents=True)
    tt.register_task_env_overrides(PARENT, {"cwd": str(parent_dir), "pin_cwd": True})

    child_cwd = tt.inherit_pinned_cwd(PARENT, CHILD)

    assert child_cwd == str(parent_dir / CHILD)
    assert os.path.isdir(child_cwd)
    child_overrides = tt.resolve_task_overrides(CHILD)
    assert child_overrides == {"cwd": child_cwd, "pin_cwd": True}


def test_remote_child_nests_posix_path(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    tt.register_task_env_overrides(PARENT, {"cwd": "/work/rna/sess1", "pin_cwd": True})

    child_cwd = tt.inherit_pinned_cwd(PARENT, CHILD)

    assert child_cwd == f"/work/rna/sess1/{CHILD}"
    assert not os.path.exists(child_cwd)  # created lazily in-sandbox
    assert tt.resolve_task_overrides(CHILD).get("pin_cwd") is True


def test_unpinned_parent_yields_nothing():
    tt.register_task_env_overrides(PARENT, {"cwd": "/somewhere"})  # not pinned
    assert tt.inherit_pinned_cwd(PARENT, CHILD) is None
    assert tt.resolve_task_overrides(CHILD) == {}


def test_missing_ids_yield_nothing():
    assert tt.inherit_pinned_cwd(None, CHILD) is None
    assert tt.inherit_pinned_cwd(PARENT, "") is None


def test_child_isolation_no_sandbox_isolation():
    """The child's pin must not give it an isolated sandbox of its own."""
    tt.register_task_env_overrides(PARENT, {"cwd": "/work/p/s", "pin_cwd": True})
    tt.inherit_pinned_cwd(PARENT, CHILD)
    assert tt._resolve_container_task_id(CHILD) == "default"


# ──────────────────────────────────────────────────────────────────────────
# kanban_create from a project-bound session (local backend)
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def kanban_home(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-caller")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    from pathlib import Path as _Path

    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    from hermes_cli import kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return tmp_path


def test_kanban_task_from_pinned_session_lands_in_project(kanban_home, tmp_path):
    from hermes_cli import kanban_db as kb
    from tools import kanban_tools as kt

    session_dir = tmp_path / "projects" / "rna" / PARENT
    session_dir.mkdir(parents=True)
    tt.register_task_env_overrides(PARENT, {"cwd": str(session_dir), "pin_cwd": True})

    out = json.loads(
        kt._handle_create(
            {"title": "analyze data", "assignee": "peer"}, task_id=PARENT
        )
    )
    assert out["ok"] is True

    conn = kb.connect()
    try:
        child = kb.get_task(conn, out["task_id"])
    finally:
        conn.close()
    assert child.workspace_kind == "dir"
    assert child.workspace_path.startswith(str(session_dir))
    assert "kanban-" in os.path.basename(child.workspace_path)
    assert os.path.isdir(child.workspace_path)


def test_kanban_explicit_workspace_beats_project(kanban_home, tmp_path):
    from hermes_cli import kanban_db as kb
    from tools import kanban_tools as kt

    session_dir = tmp_path / "projects" / "rna" / PARENT
    session_dir.mkdir(parents=True)
    tt.register_task_env_overrides(PARENT, {"cwd": str(session_dir), "pin_cwd": True})
    explicit = tmp_path / "elsewhere"
    explicit.mkdir()

    out = json.loads(
        kt._handle_create(
            {
                "title": "t",
                "assignee": "peer",
                "workspace_kind": "dir",
                "workspace_path": str(explicit),
            },
            task_id=PARENT,
        )
    )
    conn = kb.connect()
    try:
        child = kb.get_task(conn, out["task_id"])
    finally:
        conn.close()
    assert child.workspace_path == str(explicit)


def test_kanban_remote_backend_keeps_scratch(kanban_home, monkeypatch):
    """Modal-bound sessions keep scratch until volume commit/reload lands."""
    from hermes_cli import kanban_db as kb
    from tools import kanban_tools as kt

    monkeypatch.setenv("TERMINAL_ENV", "modal")
    tt.register_task_env_overrides(PARENT, {"cwd": "/work/rna/s1", "pin_cwd": True})

    out = json.loads(
        kt._handle_create({"title": "t", "assignee": "peer"}, task_id=PARENT)
    )
    conn = kb.connect()
    try:
        child = kb.get_task(conn, out["task_id"])
    finally:
        conn.close()
    assert child.workspace_kind == "scratch"


def test_kanban_unpinned_session_keeps_scratch(kanban_home):
    from hermes_cli import kanban_db as kb
    from tools import kanban_tools as kt

    out = json.loads(
        kt._handle_create({"title": "t", "assignee": "peer"}, task_id=PARENT)
    )
    conn = kb.connect()
    try:
        child = kb.get_task(conn, out["task_id"])
    finally:
        conn.close()
    assert child.workspace_kind == "scratch"


# ──────────────────────────────────────────────────────────────────────────
# delegate_tool wiring (workspace hint override)
# ──────────────────────────────────────────────────────────────────────────

def test_delegate_child_cleanup_clears_pin(monkeypatch):
    """The child-runner finally block drops the inherited pin, not others."""
    tt.register_task_env_overrides(CHILD, {"cwd": "/work/p/s/child", "pin_cwd": True})
    # Mirror the cleanup logic in delegate_tool's finally block.
    if (tt._task_env_overrides.get(CHILD) or {}).get("pin_cwd"):
        tt.clear_task_env_overrides(CHILD)
    assert tt.resolve_task_overrides(CHILD) == {}
