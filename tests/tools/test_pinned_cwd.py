"""Tests for pinned per-task working directories (``pin_cwd`` overrides).

Project-bound gateway sessions pin their cwd: many sessions share the
collapsed "default" terminal environment, so a bound session must resolve its
working directory from its own raw-keyed override — never from the shared
environment's live ``env.cwd`` tracker (which carries other sessions' ``cd``
state), and never by mutating that shared tracker itself.
"""

from __future__ import annotations

from types import SimpleNamespace

import tools.file_tools as file_tools
import tools.terminal_tool as tt


def _cleanup_override(task_id):
    tt.clear_task_env_overrides(task_id)


# ──────────────────────────────────────────────────────────────────────────
# _resolve_command_cwd precedence
# ──────────────────────────────────────────────────────────────────────────

def test_workdir_beats_pin_beats_live_beats_default():
    env = SimpleNamespace(cwd="/live")
    kw = dict(env=env, default_cwd="/default")
    assert tt._resolve_command_cwd(workdir="/explicit", pinned_cwd="/pin", **kw) == "/explicit"
    assert tt._resolve_command_cwd(workdir=None, pinned_cwd="/pin", **kw) == "/pin"
    assert tt._resolve_command_cwd(workdir=None, pinned_cwd=None, **kw) == "/live"
    env_no_live = SimpleNamespace(cwd="")
    assert (
        tt._resolve_command_cwd(
            workdir=None, pinned_cwd=None, env=env_no_live, default_cwd="/default"
        )
        == "/default"
    )


# ──────────────────────────────────────────────────────────────────────────
# register_task_env_overrides live-push behavior
# ──────────────────────────────────────────────────────────────────────────

def test_pinned_override_does_not_mutate_shared_live_env():
    env = SimpleNamespace(cwd="/shared")
    with tt._env_lock:
        tt._active_environments["pin-sess"] = env
    try:
        tt.register_task_env_overrides("pin-sess", {"cwd": "/work/p/s", "pin_cwd": True})
        assert env.cwd == "/shared"  # untouched: env is shared across sessions
    finally:
        with tt._env_lock:
            tt._active_environments.pop("pin-sess", None)
        _cleanup_override("pin-sess")


def test_unpinned_override_still_pushes_live_env():
    """ACP-style cwd overrides keep their existing live-push semantics."""
    env = SimpleNamespace(cwd="/old")
    with tt._env_lock:
        tt._active_environments["acp-sess"] = env
    try:
        tt.register_task_env_overrides("acp-sess", {"cwd": "/new/root"})
        assert env.cwd == "/new/root"
    finally:
        with tt._env_lock:
            tt._active_environments.pop("acp-sess", None)
        _cleanup_override("acp-sess")


def test_pin_cwd_override_does_not_isolate_sandbox():
    """A pin_cwd override must keep collapsing to the shared container."""
    try:
        tt.register_task_env_overrides("pin-sess", {"cwd": "/work/p/s", "pin_cwd": True})
        assert tt._resolve_container_task_id("pin-sess") == "default"
    finally:
        _cleanup_override("pin-sess")


# ──────────────────────────────────────────────────────────────────────────
# _ensure_pinned_cwd
# ──────────────────────────────────────────────────────────────────────────

class _RecordingEnv:
    def __init__(self):
        self.commands = []
        self.cwd = "/shared"

    def execute(self, command, cwd=None, timeout=None):
        self.commands.append((command, cwd))
        return SimpleNamespace(exit_code=0, output="")


def test_ensure_pinned_cwd_runs_mkdir_once_per_env():
    env = _RecordingEnv()
    tt._ensure_pinned_cwd(env, "/work/proj/sess")
    tt._ensure_pinned_cwd(env, "/work/proj/sess")
    mkdirs = [c for c, _ in env.commands if c.startswith("mkdir -p")]
    assert len(mkdirs) == 1
    assert "/work/proj/sess" in mkdirs[0]

    # A NEW env object (recreated sandbox) is ensured again.
    env2 = _RecordingEnv()
    tt._ensure_pinned_cwd(env2, "/work/proj/sess")
    assert any(c.startswith("mkdir -p") for c, _ in env2.commands)


def test_ensure_pinned_cwd_failure_is_soft():
    class _FailingEnv:
        def execute(self, command, cwd=None, timeout=None):
            raise RuntimeError("sandbox unavailable")

    tt._ensure_pinned_cwd(_FailingEnv(), "/work/p/s")  # must not raise


# ──────────────────────────────────────────────────────────────────────────
# file_tools base-dir resolution
# ──────────────────────────────────────────────────────────────────────────

def test_pinned_override_outranks_live_cwd_for_file_paths(monkeypatch, tmp_path):
    pinned = str(tmp_path / "proj" / "sess")
    try:
        tt.register_task_env_overrides("pin-sess", {"cwd": pinned, "pin_cwd": True})
        monkeypatch.setattr(
            file_tools, "_get_live_tracking_cwd", lambda task_id="default": "/other/live"
        )
        assert str(file_tools._resolve_base_dir("pin-sess")) == str(
            (tmp_path / "proj" / "sess").resolve()
        )
    finally:
        _cleanup_override("pin-sess")


def test_unpinned_override_still_below_live_cwd(monkeypatch, tmp_path):
    unpinned = str(tmp_path / "acp-root")
    live = str(tmp_path / "live-root")
    try:
        tt.register_task_env_overrides("acp-sess", {"cwd": unpinned})
        monkeypatch.setattr(
            file_tools, "_get_live_tracking_cwd", lambda task_id="default": live
        )
        assert str(file_tools._resolve_base_dir("acp-sess")) == str(
            (tmp_path / "live-root").resolve()
        )
    finally:
        _cleanup_override("acp-sess")
