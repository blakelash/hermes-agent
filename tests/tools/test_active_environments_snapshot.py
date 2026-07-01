"""Tests for the operator-Dashboard active-environments snapshot.

``get_active_environments_snapshot()`` is a REAL snapshot of the terminal
environment registry — it must reflect the actual architecture (live envs +
the configured-but-not-started backend), never fictional peers. These assert
the entry shape, the running/idle status derived from the real process
registry, and the "configured" fallback row.
"""

from __future__ import annotations

import contextlib

import tools.terminal_tool as tt


# The backend slug is derived from type(env).__name__, so the fakes must carry
# the SAME class names as the real backends (tools/environments/*.py).
class LocalEnvironment:
    def __init__(self, cwd: str):
        self.cwd = cwd


class DockerEnvironment:
    def __init__(self, container_id: str, image: str, cwd: str):
        self._container_id = container_id
        self._image = image
        self.cwd = cwd


class SSHEnvironment:
    def __init__(self, user: str, host: str, cwd: str):
        self.user = user
        self.host = host
        self.cwd = cwd


@contextlib.contextmanager
def _registry(active: dict, last_activity: dict | None, running_task_ids: set):
    """Install fake env-registry state + a stub process registry.

    Restores everything on exit so suite isolation holds even though these are
    module-level globals.
    """
    with tt._env_lock:
        saved_active = dict(tt._active_environments)
        saved_last = dict(tt._last_activity)
        tt._active_environments.clear()
        tt._active_environments.update(active)
        tt._last_activity.clear()
        tt._last_activity.update(last_activity or {})

    import tools.process_registry as pr

    saved_has = pr.process_registry.has_active_processes
    pr.process_registry.has_active_processes = lambda task_id: task_id in running_task_ids
    try:
        yield
    finally:
        pr.process_registry.has_active_processes = saved_has
        with tt._env_lock:
            tt._active_environments.clear()
            tt._active_environments.update(saved_active)
            tt._last_activity.clear()
            tt._last_activity.update(saved_last)


def test_backend_slug_strips_environment_suffix():
    assert tt._env_backend_name(LocalEnvironment("/x")) == "local"
    assert tt._env_backend_name(DockerEnvironment("c", "i", "/x")) == "docker"
    assert tt._env_backend_name(SSHEnvironment("u", "h", "/x")) == "ssh"


def test_local_env_snapshot_shape(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    with _registry(
        {"task-a": LocalEnvironment("/home/me/project")},
        {"task-a": tt.time.time() - 7},
        running_task_ids=set(),
    ):
        rows = tt.get_active_environments_snapshot()

    live = [r for r in rows if r["id"] == "task-a"]
    assert len(live) == 1
    row = live[0]
    assert set(row) == {"id", "backend", "label", "status", "detail", "idle_seconds"}
    assert row["backend"] == "local"
    assert row["label"] == "local · project"
    assert row["status"] == "idle"
    assert row["detail"] == "/home/me/project"
    assert row["idle_seconds"] >= 6
    # local is the live backend → no separate "configured" row.
    assert all(r["id"] != "configured" for r in rows)


def test_running_status_from_process_registry(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    with _registry(
        {"busy": LocalEnvironment("/w")},
        {"busy": tt.time.time()},
        running_task_ids={"busy"},
    ):
        rows = tt.get_active_environments_snapshot()
    assert [r["status"] for r in rows if r["id"] == "busy"] == ["running"]


def test_docker_and_ssh_labels(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    with _registry(
        {
            "d": DockerEnvironment("abcdef0123456789", "python:3.11", "/app"),
            "s": SSHEnvironment("ubuntu", "10.0.0.5", "/srv"),
        },
        None,
        running_task_ids=set(),
    ):
        rows = {r["id"]: r for r in tt.get_active_environments_snapshot()}

    assert rows["d"]["label"] == "docker · abcdef012345"  # short (12-char) id
    assert rows["d"]["detail"] == "python:3.11"
    assert rows["s"]["label"] == "ssh · ubuntu@10.0.0.5"
    assert rows["s"]["detail"] == "/srv"


def test_configured_row_when_no_active_env(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    with _registry({}, None, running_task_ids=set()):
        rows = tt.get_active_environments_snapshot()

    assert len(rows) == 1
    assert rows[0] == {
        "id": "configured",
        "backend": "docker",
        "label": "docker",
        "status": "configured",
        "detail": "not started",
        "idle_seconds": 0,
    }


def test_configured_row_suppressed_when_backend_already_live(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    with _registry(
        {"d": DockerEnvironment("c1", "img", "/app")},
        None,
        running_task_ids=set(),
    ):
        rows = tt.get_active_environments_snapshot()
    # docker is live → no duplicate "configured" docker row.
    assert all(r["id"] != "configured" for r in rows)
    assert [r["backend"] for r in rows] == ["docker"]


def test_configured_row_added_when_different_backend_live(monkeypatch):
    # A local env is live but config says docker → both surface: the live local
    # row AND a configured docker row (the next isolated command's backend).
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    with _registry(
        {"l": LocalEnvironment("/w")},
        None,
        running_task_ids=set(),
    ):
        rows = tt.get_active_environments_snapshot()
    ids = {r["id"] for r in rows}
    assert "l" in ids and "configured" in ids
    configured = next(r for r in rows if r["id"] == "configured")
    assert configured["backend"] == "docker"
