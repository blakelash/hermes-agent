"""Tests for project binding of messaging-gateway sessions.

Covers the backend-aware working-directory derivation
(``gateway/project_binding.py``), the sticky per-chat default applied when the
session store mints a session, and binding persistence across /new resets and
store restarts.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import gateway.project_binding as pb
import tools.projects as projects
import tools.terminal_tool as tt
from gateway.config import GatewayConfig, Platform, SessionResetPolicy
from gateway.session import SessionSource, SessionStore
from hermes_state import SessionDB


@pytest.fixture(autouse=True)
def _local_backend(monkeypatch):
    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    monkeypatch.delenv("TERMINAL_MODAL_VOLUMES", raising=False)


def _make_store(tmp_path, db=None):
    config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(sessions_dir=tmp_path / "sessions", config=config)
    store._db = db
    store._loaded = True
    return store


def _source(chat_id="67890"):
    return SessionSource(
        platform=Platform.TELEGRAM, user_id="12345", chat_id=chat_id, user_name="u"
    )


# ──────────────────────────────────────────────────────────────────────────
# session_workdir / resolve_volume_root — backend-aware derivation
# ──────────────────────────────────────────────────────────────────────────

def test_local_backend_uses_host_project_dir():
    p = projects.create_project("RNA")
    wd = pb.session_workdir("rna", "sess1")
    assert wd == os.path.join(p["cwd"], "sess1")


def test_local_backend_unknown_project_is_unbound():
    assert pb.session_workdir("ghost", "sess1") is None


def test_remote_backend_uses_volume_path(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    assert pb.session_workdir("rna", "sess1") == "/work/rna/sess1"


def test_remote_volume_root_from_configured_mounts(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    monkeypatch.setenv(
        "TERMINAL_MODAL_VOLUMES", '[{"name": "sci", "mount_path": "/data"}]'
    )
    assert pb.resolve_volume_root() == "/data"
    assert pb.session_workdir("rna", "s1") == "/data/rna/s1"


def test_read_only_volume_skipped_for_project_root(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    monkeypatch.setenv(
        "TERMINAL_MODAL_VOLUMES",
        '[{"name": "ro", "mount_path": "/datasets", "read_only": true}]',
    )
    assert pb.resolve_volume_root() == "/work"


# ──────────────────────────────────────────────────────────────────────────
# register_session_workdir / release_session_workdir
# ──────────────────────────────────────────────────────────────────────────

def test_register_local_creates_dir_and_pins_override():
    projects.create_project("RNA")
    try:
        wd = pb.register_session_workdir("sess1", "rna")
        assert wd and Path(wd).is_dir()
        overrides = tt.resolve_task_overrides("sess1")
        assert overrides.get("cwd") == wd and overrides.get("pin_cwd") is True
    finally:
        tt.clear_task_env_overrides("sess1")


def test_register_remote_pins_volume_path_without_host_dir(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    try:
        wd = pb.register_session_workdir("sess1", "rna")
        assert wd == "/work/rna/sess1"
        assert not os.path.exists(wd)  # nothing host-side
        assert tt.resolve_task_overrides("sess1").get("pin_cwd") is True
    finally:
        tt.clear_task_env_overrides("sess1")


def test_release_clears_only_pinned_overrides():
    projects.create_project("RNA")
    pb.register_session_workdir("sess1", "rna")
    pb.release_session_workdir("sess1")
    assert tt.resolve_task_overrides("sess1") == {}

    # An isolation override (not ours) survives release.
    tt.register_task_env_overrides("bench1", {"docker_image": "img"})
    try:
        pb.release_session_workdir("bench1")
        assert tt.resolve_task_overrides("bench1") == {"docker_image": "img"}
    finally:
        tt.clear_task_env_overrides("bench1")


# ──────────────────────────────────────────────────────────────────────────
# project_prompt_hint
# ──────────────────────────────────────────────────────────────────────────

def test_prompt_hint_names_project_and_dir():
    p = projects.create_project("RNA Analysis")
    hint = pb.project_prompt_hint("rna-analysis", "sess1")
    assert "RNA Analysis" in hint and "rna-analysis" in hint
    assert os.path.join(p["cwd"], "sess1") in hint


def test_prompt_hint_remote_mentions_persistence(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    hint = pb.project_prompt_hint("rna", "sess1")
    assert "/work/rna/sess1" in hint
    assert "persistent" in hint
    assert "/work/rna" in hint


def test_prompt_hint_empty_when_unbound():
    assert pb.project_prompt_hint("ghost", "sess1") == ""
    assert pb.project_prompt_hint("", "sess1") == ""


# ──────────────────────────────────────────────────────────────────────────
# Sticky chat defaults through the session store
# ──────────────────────────────────────────────────────────────────────────

def test_new_session_adopts_chat_default(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = _make_store(tmp_path, db=db)
    source = _source()
    key = store._generate_session_key(source)
    db.set_chat_project_default(key, "rna")

    entry = store.get_or_create_session(source)
    assert entry.project_slug == "rna"
    row = db.get_session(entry.session_id)
    assert row["project"] == "rna"
    assert row["gateway_session_key"] == key


def test_unpinned_chat_creates_unbound_session(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = _make_store(tmp_path, db=db)
    entry = store.get_or_create_session(_source())
    assert entry.project_slug == ""
    row = db.get_session(entry.session_id)
    assert row["project"] is None
    assert row["gateway_session_key"] == entry.session_key


def test_reset_preserves_sticky_binding(tmp_path):
    """/new starts a fresh session in the SAME project (sticky semantics)."""
    db = SessionDB(tmp_path / "state.db")
    store = _make_store(tmp_path, db=db)
    source = _source()
    first = store.get_or_create_session(source)
    db.set_chat_project_default(first.session_key, "rna")

    second = store.reset_session(first.session_key)
    assert second.session_id != first.session_id
    assert second.project_slug == "rna"
    assert db.get_session(second.session_id)["project"] == "rna"


def test_binding_survives_store_restart(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    store = _make_store(tmp_path, db=db)
    source = _source()
    key = store._generate_session_key(source)
    db.set_chat_project_default(key, "rna")
    entry = store.get_or_create_session(source)

    # Fresh store instance loads sessions.json from disk (simulated restart).
    config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
    store2 = SessionStore(sessions_dir=tmp_path / "sessions", config=config)
    store2._db = db
    reloaded = store2.get_or_create_session(source)
    assert reloaded.session_id == entry.session_id
    assert reloaded.project_slug == "rna"


def test_db_failure_yields_unbound_session(tmp_path):
    """A broken defaults lookup must never fail message handling."""

    class _BrokenDB(SessionDB):
        def get_chat_project_default(self, session_key):
            raise RuntimeError("db down")

    db = _BrokenDB(tmp_path / "state.db")
    store = _make_store(tmp_path, db=db)
    entry = store.get_or_create_session(_source())
    assert entry.project_slug == ""
