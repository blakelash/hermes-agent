"""Tests for the session project binding columns and chat project defaults.

The explicit ``sessions.project`` column is how sessions whose work lives
off-host (Modal volume) stay grouped under a project — it must win over
cwd-prefix derivation, survive schema migration from older databases, and
surface through ``list_sessions_rich``. ``chat_project_defaults`` is the
sticky per-chat binding shared between the messaging gateway and the desktop
backend (separate processes meeting in the same WAL database).
"""

from __future__ import annotations

import sqlite3

import pytest

from hermes_state import SessionDB


@pytest.fixture()
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


# ──────────────────────────────────────────────────────────────────────────
# Schema migration
# ──────────────────────────────────────────────────────────────────────────

def test_old_schema_db_gains_project_columns(tmp_path):
    """A pre-existing DB without the new columns migrates on open."""
    path = tmp_path / "state.db"
    # Build a current DB, then strip the new columns to simulate a database
    # created before they existed.
    old = SessionDB(path)
    old.create_session("old1", "telegram")
    old._conn.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE sessions DROP COLUMN project")
    conn.execute("ALTER TABLE sessions DROP COLUMN gateway_session_key")
    conn.commit()
    conn.close()

    db = SessionDB(path)
    cols = {
        row[1]
        for row in db._conn.execute("PRAGMA table_info('sessions')").fetchall()
    }
    assert {"project", "gateway_session_key"} <= cols
    # Old row is intact and reads back with a NULL project.
    row = db._conn.execute(
        "SELECT project, gateway_session_key FROM sessions WHERE id = 'old1'"
    ).fetchone()
    assert tuple(row) == (None, None)


# ──────────────────────────────────────────────────────────────────────────
# Session project column
# ──────────────────────────────────────────────────────────────────────────

def test_create_session_stores_project_and_gateway_key(db):
    db.create_session(
        "s1", "telegram", project="rna", gateway_session_key="telegram:dm:42"
    )
    row = db._conn.execute(
        "SELECT project, gateway_session_key FROM sessions WHERE id = 's1'"
    ).fetchone()
    assert tuple(row) == ("rna", "telegram:dm:42")


def test_update_session_project_sets_and_unbinds(db):
    db.create_session("s1", "telegram")
    db.update_session_project("s1", "rna")
    assert db.get_session("s1")["project"] == "rna"
    # Empty string unbinds back to NULL, not "".
    db.update_session_project("s1", "")
    assert db.get_session("s1")["project"] is None


def test_update_session_project_ignores_empty_session_id(db):
    db.update_session_project("", "rna")  # must not raise


def test_list_sessions_rich_carries_project(db):
    db.create_session("bound", "slack", project="rna")
    db.create_session("unbound", "slack")
    rows = {s["id"]: s for s in db.list_sessions_rich(limit=10)}
    assert rows["bound"]["project"] == "rna"
    assert rows["unbound"]["project"] is None


# ──────────────────────────────────────────────────────────────────────────
# Chat project defaults (sticky bindings)
# ──────────────────────────────────────────────────────────────────────────

def test_chat_default_round_trip(db):
    assert db.get_chat_project_default("telegram:dm:42") is None
    db.set_chat_project_default("telegram:dm:42", "rna")
    assert db.get_chat_project_default("telegram:dm:42") == "rna"


def test_chat_default_upsert_overwrites(db):
    db.set_chat_project_default("k", "first")
    db.set_chat_project_default("k", "second")
    assert db.get_chat_project_default("k") == "second"


def test_chat_default_clear(db):
    db.set_chat_project_default("k", "rna")
    db.clear_chat_project_default("k")
    assert db.get_chat_project_default("k") is None
    # Clearing an unknown key is a no-op, not an error.
    db.clear_chat_project_default("never-set")


def test_chat_default_empty_args_are_noops(db):
    db.set_chat_project_default("", "rna")
    db.set_chat_project_default("k", "")
    assert db.get_chat_project_default("") is None
    assert db.get_chat_project_default("k") is None


def test_chat_default_visible_to_second_connection(tmp_path):
    """Two SessionDB instances on one file see each other's writes (the
    messaging gateway and desktop backend are separate processes)."""
    path = tmp_path / "state.db"
    writer = SessionDB(path)
    reader = SessionDB(path)
    writer.set_chat_project_default("telegram:dm:42", "rna")
    assert reader.get_chat_project_default("telegram:dm:42") == "rna"
