"""Tests for the project + global-dashboard JSON-RPC surface (server.py).

Covers projects.{list,create,rename}, the project param on files.{tree,read},
session→project binding with per-session subdir + project-root containment, and
the global vs per-session dashboard.snapshot. Sessions are seeded into
_sessions directly (the pattern the other gateway server tests use); the
project registry + cost sum write into the per-test isolated HERMES_HOME.
"""

from __future__ import annotations

import os

import pytest

import tui_gateway.server as srv
import tools.projects as projects
import tools.approval as approval


@pytest.fixture()
def clean_sessions():
    with srv._sessions_lock:
        saved = dict(srv._sessions)
        srv._sessions.clear()
    yield
    with srv._sessions_lock:
        srv._sessions.clear()
        srv._sessions.update(saved)


def _call(method: str, params: dict) -> dict:
    return srv._methods[method](1, params)


def _seed_session(sid: str, key: str, cwd: str, *, project_slug: str = "", agent=None):
    with srv._sessions_lock:
        srv._sessions[sid] = {
            "session_key": key,
            "agent": agent,
            "cwd": cwd,
            "explicit_cwd": True,
            "project_slug": project_slug,
            "running": False,
        }
    with approval._lock:
        approval._gateway_queues.pop(key, None)


def _enqueue(session_key: str, data: dict):
    entry = approval._ApprovalEntry(data)
    data["request_id"] = entry.request_id
    with approval._lock:
        approval._gateway_queues.setdefault(session_key, []).append(entry)
    return entry


@pytest.fixture()
def rpc_db(tmp_path, monkeypatch):
    """Real SessionDB wired into the server's _get_db()."""
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    monkeypatch.setattr(srv, "_get_db", lambda: db)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# projects.{list,create,rename}
# ---------------------------------------------------------------------------


def test_projects_create_list_rename(clean_sessions):
    res = _call("projects.create", {"name": "Demo"})["result"]
    assert res["project"]["slug"] == "demo"
    assert os.path.isdir(res["project"]["cwd"])  # managed dir created

    listed = _call("projects.list", {})["result"]["projects"]
    assert len(listed) == 1
    p = listed[0]
    assert set(p) == {"slug", "name", "cwd", "sessionCount", "sessionIds", "status"}
    assert p["sessionCount"] == 0 and p["sessionIds"] == [] and p["status"] == "idle"

    res2 = _call("projects.rename", {"slug": "demo", "name": "Renamed"})["result"]
    assert res2["project"]["name"] == "Renamed"
    assert res2["project"]["slug"] == "demo"


def test_projects_create_rejects_empty_name(clean_sessions):
    env = _call("projects.create", {"name": "  "})
    assert env["error"]["code"] == 4019


def test_projects_list_counts_live_sessions(clean_sessions):
    p = projects.create_project("Demo")
    root = p["cwd"]
    _seed_session("s1", "k1", root, project_slug="demo")
    _seed_session("s2", "k2", os.path.join(root, "s2"), project_slug="demo")

    demo = next(x for x in _call("projects.list", {})["result"]["projects"] if x["slug"] == "demo")
    assert demo["sessionCount"] == 2
    assert set(demo["sessionIds"]) == {"s1", "s2"}
    assert demo["status"] == "idle"


def test_projects_list_groups_by_cwd_when_unbound(clean_sessions):
    # A session with no explicit project_slug but whose cwd is under a project
    # root is still grouped under that project (project_for_cwd detection).
    p = projects.create_project("Demo")
    _seed_session("s1", "k1", os.path.join(p["cwd"], "sub"))  # no project_slug
    demo = next(x for x in _call("projects.list", {})["result"]["projects"] if x["slug"] == "demo")
    assert demo["sessionCount"] == 1


def test_project_status_blocked_on_pending_approval(clean_sessions):
    p = projects.create_project("Demo")
    _seed_session("s1", "k1", p["cwd"], project_slug="demo")
    _enqueue("k1", {"command": "rm -rf x", "description": "recursive delete"})
    demo = next(x for x in _call("projects.list", {})["result"]["projects"] if x["slug"] == "demo")
    assert demo["status"] == "blocked"


def test_project_status_working_when_running(clean_sessions):
    p = projects.create_project("Demo")
    _seed_session("s1", "k1", p["cwd"], project_slug="demo")
    with srv._sessions_lock:
        srv._sessions["s1"]["running"] = True
    demo = next(x for x in _call("projects.list", {})["result"]["projects"] if x["slug"] == "demo")
    assert demo["status"] == "working"


# ---------------------------------------------------------------------------
# session → project binding: per-session subdir + containment
# ---------------------------------------------------------------------------


def test_bind_defaults_to_session_subdir(clean_sessions):
    p = projects.create_project("Demo")
    with srv._sessions_lock:
        srv._sessions["s1"] = {"session_key": "k1", "agent": None, "cwd": "", "running": False}
    project = srv._bind_session_project(srv._sessions["s1"], "demo", "s1")
    assert project["slug"] == "demo"
    # cwd defaulted to <project_cwd>/<sid> and the dir was created.
    expected = os.path.join(os.path.realpath(p["cwd"]), "s1")
    assert os.path.realpath(srv._sessions["s1"]["cwd"]) == expected
    assert os.path.isdir(srv._sessions["s1"]["cwd"])


def test_set_cwd_within_project_ok_escape_rejected(clean_sessions):
    p = projects.create_project("Demo")
    inside = os.path.join(p["cwd"], "inside")
    os.makedirs(inside, exist_ok=True)
    _seed_session("s1", "k1", p["cwd"], project_slug="demo")
    session = srv._sessions["s1"]

    srv._set_session_cwd(session, inside)  # within → ok
    assert os.path.realpath(session["cwd"]) == os.path.realpath(inside)

    with pytest.raises(ValueError):
        srv._set_session_cwd(session, os.path.dirname(os.path.realpath(p["cwd"])))


def test_session_cwd_set_rpc_enforces_containment(clean_sessions):
    p = projects.create_project("Demo")
    ok = os.path.join(p["cwd"], "ok")
    os.makedirs(ok, exist_ok=True)
    _seed_session("s1", "k1", p["cwd"], project_slug="demo")

    assert "result" in _call("session.cwd.set", {"session_id": "s1", "cwd": ok})
    bad = _call("session.cwd.set", {"session_id": "s1", "cwd": os.path.dirname(p["cwd"])})
    assert bad["error"]["code"] == 4017


def test_unscoped_session_cwd_unconstrained(tmp_path, clean_sessions):
    # No project → cwd can be set anywhere that exists (unchanged behavior).
    other = tmp_path / "elsewhere"
    other.mkdir()
    _seed_session("s1", "k1", str(tmp_path))
    assert "result" in _call("session.cwd.set", {"session_id": "s1", "cwd": str(other)})


# ---------------------------------------------------------------------------
# files.tree / files.read with project param (slug or cwd)
# ---------------------------------------------------------------------------


def test_files_tree_with_project_slug(clean_sessions, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    p = projects.create_project("Demo")
    (open(os.path.join(p["cwd"], "a.txt"), "w", encoding="utf-8")).write("hi")
    _seed_session("s1", "k1", "/")  # session cwd elsewhere; project roots listing

    res = _call("files.tree", {"session_id": "s1", "project": "demo", "path": ""})["result"]
    assert os.path.realpath(res["root"]) == os.path.realpath(p["cwd"])
    assert [e["name"] for e in res["entries"]] == ["a.txt"]


def test_files_tree_with_project_cwd(clean_sessions, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    p = projects.create_project("Demo")
    sub = os.path.join(p["cwd"], "s1")
    os.makedirs(sub, exist_ok=True)
    _seed_session("s1", "k1", "/")
    # Pass an absolute cwd under the project instead of the slug.
    res = _call("files.tree", {"session_id": "s1", "project": sub})["result"]
    # Resolves to the PROJECT root (containment), not the subdir.
    assert os.path.realpath(res["root"]) == os.path.realpath(p["cwd"])


def test_files_tree_unknown_project(clean_sessions, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    _seed_session("s1", "k1", "/tmp")
    env = _call("files.tree", {"session_id": "s1", "project": "ghost"})
    assert env["error"]["code"] == 4019


def test_files_read_with_project(clean_sessions, monkeypatch):
    monkeypatch.setattr(srv, "_register_session_cwd", lambda *_a, **_k: None)
    p = projects.create_project("Demo")
    with open(os.path.join(p["cwd"], "n.txt"), "w", encoding="utf-8") as f:
        f.write("alpha\nbeta\n")
    _seed_session("s1", "k1", "/")
    res = _call("files.read", {"session_id": "s1", "project": "demo", "path": "n.txt"})["result"]
    assert "alpha" in res["content"] and res["total_lines"] == 2


# ---------------------------------------------------------------------------
# dashboard.snapshot — global vs per-session
# ---------------------------------------------------------------------------


def test_global_snapshot_spans_sessions(clean_sessions, monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.list_active_subagents", lambda: [])
    monkeypatch.setattr("tools.terminal_tool.get_active_environments_snapshot", lambda: [])
    p = projects.create_project("Demo")
    _seed_session("s1", "k1", p["cwd"], project_slug="demo")
    _seed_session("s2", "k2", "/tmp")
    _enqueue("k1", {"command": "rm -rf x", "description": "recursive delete"})
    _enqueue("k2", {"command": "git push -f", "description": "force push"})

    res = _call("dashboard.snapshot", {})["result"]
    assert {"needs", "specialists", "environments", "projects", "cost", "timestamp"} <= set(res)
    # Cross-session needs tagged with originating session_id (mapped from key).
    by_sid = {n["session_id"]: n for n in res["needs"]}
    assert set(by_sid) == {"s1", "s2"}
    assert by_sid["s1"]["description"] == "recursive delete"
    assert any(x["slug"] == "demo" for x in res["projects"])


def test_global_needs_drops_sessions_with_no_live_sid(clean_sessions, monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.list_active_subagents", lambda: [])
    monkeypatch.setattr("tools.terminal_tool.get_active_environments_snapshot", lambda: [])
    # An approval queued under a key with NO live session → not surfaced (it
    # can't be responded to from the Dashboard).
    _enqueue("orphan-key", {"command": "x", "description": "orphan"})
    res = _call("dashboard.snapshot", {})["result"]
    assert res["needs"] == []
    # Clean up the orphan queue we created outside _seed_session.
    with approval._lock:
        approval._gateway_queues.pop("orphan-key", None)


def test_per_session_snapshot_scopes_needs(clean_sessions, monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.list_active_subagents", lambda: [])
    monkeypatch.setattr("tools.terminal_tool.get_active_environments_snapshot", lambda: [])
    _seed_session("s1", "k1", "/tmp")
    _seed_session("s2", "k2", "/tmp")
    _enqueue("k1", {"command": "a", "description": "d1"})
    _enqueue("k2", {"command": "b", "description": "d2"})

    res = _call("dashboard.snapshot", {"session_id": "s1"})["result"]
    assert len(res["needs"]) == 1
    assert res["needs"][0]["description"] == "d1"
    assert "projects" in res


def test_global_cost_from_session_db(clean_sessions, monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.list_active_subagents", lambda: [])
    monkeypatch.setattr("tools.terminal_tool.get_active_environments_snapshot", lambda: [])

    class _FakeDB:
        def sum_session_cost(self, since=None):
            return {"cost_usd": 2.5, "input": 100, "output": 40, "total": 140, "calls": 7}

    monkeypatch.setattr(srv, "_get_db", lambda: _FakeDB())
    res = _call("dashboard.snapshot", {})["result"]
    assert res["cost"]["cost_usd"] == 2.5
    assert res["cost"]["total"] == 140 and res["cost"]["calls"] == 7


# ---------------------------------------------------------------------------
# session.list — explicit project column wins over cwd derivation
# ---------------------------------------------------------------------------


def test_session_list_prefers_explicit_project_column(clean_sessions, rpc_db):
    projects.create_project("Demo")
    # Messaging session: off-host work → no meaningful cwd, explicit column.
    rpc_db.create_session("tg1", "telegram", project="demo")
    # TUI session: cwd under the project root, no explicit column.
    demo_root = projects.get_project("demo")["cwd"]
    rpc_db.create_session("tui1", "tui", cwd=os.path.join(demo_root, "tui1"))
    # Unbound session.
    rpc_db.create_session("plain1", "cli")

    rows = {s["id"]: s for s in _call("session.list", {})["result"]["sessions"]}
    assert rows["tg1"]["project"] == "demo"
    assert rows["tui1"]["project"] == "demo"
    assert rows["plain1"]["project"] == ""


def test_session_list_explicit_beats_conflicting_cwd(clean_sessions, rpc_db):
    projects.create_project("Alpha")
    projects.create_project("Beta")
    alpha_root = projects.get_project("alpha")["cwd"]
    # cwd says alpha, explicit column says beta — explicit wins (retagged row).
    rpc_db.create_session(
        "s1", "telegram", project="beta", cwd=os.path.join(alpha_root, "s1")
    )
    rows = {s["id"]: s for s in _call("session.list", {})["result"]["sessions"]}
    assert rows["s1"]["project"] == "beta"


# ---------------------------------------------------------------------------
# session.project.set — drag-to-reassign for messaging/history rows
# ---------------------------------------------------------------------------


def test_project_set_retags_row_and_pins_chat(clean_sessions, rpc_db):
    projects.create_project("Demo")
    rpc_db.create_session(
        "tg1", "telegram", gateway_session_key="telegram:12:34"
    )

    res = _call("session.project.set", {"session_id": "tg1", "project": "demo"})["result"]

    assert res == {"project": "demo", "chat_default_updated": True}
    assert rpc_db.get_session("tg1")["project"] == "demo"
    assert rpc_db.get_chat_project_default("telegram:12:34") == "demo"
    # Grouping is immediately visible in the list feed.
    rows = {s["id"]: s for s in _call("session.list", {})["result"]["sessions"]}
    assert rows["tg1"]["project"] == "demo"


def test_project_set_clear_unpins(clean_sessions, rpc_db):
    projects.create_project("Demo")
    rpc_db.create_session("tg1", "telegram", project="demo",
                          gateway_session_key="telegram:12:34")
    rpc_db.set_chat_project_default("telegram:12:34", "demo")

    res = _call("session.project.set", {"session_id": "tg1", "project": ""})["result"]

    assert res["project"] == "" and res["chat_default_updated"] is True
    assert rpc_db.get_session("tg1")["project"] is None
    assert rpc_db.get_chat_project_default("telegram:12:34") is None


def test_project_set_row_without_chat_key_retags_only(clean_sessions, rpc_db):
    """Pre-existing rows lack gateway_session_key: retag works, no pin."""
    projects.create_project("Demo")
    rpc_db.create_session("old1", "telegram")

    res = _call("session.project.set", {"session_id": "old1", "project": "demo"})["result"]

    assert res == {"project": "demo", "chat_default_updated": False}
    assert rpc_db.get_session("old1")["project"] == "demo"


def test_project_set_rejects_unknowns(clean_sessions, rpc_db):
    projects.create_project("Demo")
    rpc_db.create_session("tg1", "telegram")

    err = _call("session.project.set", {"session_id": "tg1", "project": "ghost"})["error"]
    assert err["code"] == 4018

    err = _call("session.project.set", {"session_id": "nope", "project": "demo"})["error"]
    assert err["code"] == 4018

    err = _call("session.project.set", {"project": "demo"})["error"]
    assert err["code"] == 4016


def test_project_set_rejects_live_workspace_session(clean_sessions, rpc_db):
    """Live sessions must rebind via session.cwd.set — retagging them would
    split UI grouping from the runtime session's actual project/cwd."""
    projects.create_project("Demo")
    rpc_db.create_session("live1", "tui")
    _seed_session("live1", "k1", "/")

    err = _call("session.project.set", {"session_id": "live1", "project": "demo"})["error"]

    assert err["code"] == 4009
    assert "session.cwd.set" in err["message"]
    assert rpc_db.get_session("live1")["project"] is None  # untouched


# ---------------------------------------------------------------------------
# Remote-backend project workdir (desktop sessions on Modal work on the volume)
# ---------------------------------------------------------------------------


def test_terminal_cwd_bound_session_remote_uses_volume_path(clean_sessions, monkeypatch):
    """A project-bound desktop session on a remote backend must work in the
    project's per-session volume directory, not the global TERMINAL_CWD —
    otherwise the binding is grouping-only and work lands in an ephemeral /."""
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    monkeypatch.setenv("TERMINAL_CWD", "/somewhere/global")
    projects.create_project("Demo")
    _seed_session("s1", "key1", "/host/projects/demo/key1", project_slug="demo")

    assert srv._terminal_task_cwd(srv._sessions["s1"]) == "/work/demo/key1"


def test_terminal_cwd_honors_configured_volume_root(clean_sessions, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    monkeypatch.setenv(
        "TERMINAL_MODAL_VOLUMES", '[{"name": "sci", "mount_path": "/data"}]'
    )
    projects.create_project("Demo")
    _seed_session("s1", "key1", "", project_slug="demo")

    assert srv._terminal_task_cwd(srv._sessions["s1"]) == "/data/demo/key1"


def test_terminal_cwd_unbound_remote_falls_back_to_global(clean_sessions, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    monkeypatch.setenv("TERMINAL_CWD", "/somewhere/global")
    _seed_session("s1", "key1", "/host/whatever")

    assert srv._terminal_task_cwd(srv._sessions["s1"]) == "/somewhere/global"


def test_terminal_cwd_bound_session_local_keeps_host_path(clean_sessions, monkeypatch, tmp_path):
    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    host_dir = str(tmp_path / "projects" / "demo" / "key1")
    _seed_session("s1", "key1", host_dir, project_slug="demo")

    assert srv._terminal_task_cwd(srv._sessions["s1"]) == host_dir


def test_register_session_cwd_pins_remote_project_sessions(clean_sessions, monkeypatch):
    import tools.terminal_tool as tt

    monkeypatch.setenv("TERMINAL_ENV", "modal")
    projects.create_project("Demo")
    _seed_session("s1", "key1", "", project_slug="demo")
    try:
        srv._register_session_cwd(srv._sessions["s1"])
        overrides = tt.resolve_task_overrides("key1")
        assert overrides == {"cwd": "/work/demo/key1", "pin_cwd": True}
    finally:
        tt.clear_task_env_overrides("key1")


def test_register_session_cwd_local_stays_unpinned(clean_sessions, monkeypatch, tmp_path):
    import tools.terminal_tool as tt

    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    host_dir = str(tmp_path / "projects" / "demo" / "key1")
    _seed_session("s1", "key1", host_dir, project_slug="demo")
    try:
        srv._register_session_cwd(srv._sessions["s1"])
        overrides = tt.resolve_task_overrides("key1")
        assert overrides.get("cwd") == host_dir
        assert "pin_cwd" not in overrides
    finally:
        tt.clear_task_env_overrides("key1")
