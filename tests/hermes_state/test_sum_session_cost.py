"""Tests for SessionDB.sum_session_cost — the profile-global cost aggregate
powering the operator Dashboard's global snapshot."""

from __future__ import annotations

import time

import pytest

from hermes_state import SessionDB


@pytest.fixture()
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _add(db, sid, *, cost, inp, out, calls, started_at=None):
    db.create_session(sid, "tui")
    db.update_token_counts(
        sid,
        input_tokens=inp,
        output_tokens=out,
        estimated_cost_usd=cost,
        api_call_count=calls,
        absolute=True,
    )
    if started_at is not None:
        with db._lock:
            db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (started_at, sid))
            db._conn.commit()


def test_empty_db_sums_to_zero(db):
    assert db.sum_session_cost() == {
        "cost_usd": 0.0,
        "input": 0,
        "output": 0,
        "total": 0,
        "calls": 0,
    }


def test_sums_across_sessions(db):
    _add(db, "a", cost=1.5, inp=100, out=40, calls=3)
    _add(db, "b", cost=2.0, inp=10, out=5, calls=1)
    out = db.sum_session_cost()
    assert out["cost_usd"] == pytest.approx(3.5)
    assert out["input"] == 110
    assert out["output"] == 45
    assert out["total"] == 155
    assert out["calls"] == 4


def test_excludes_archived(db):
    _add(db, "a", cost=1.0, inp=10, out=10, calls=1)
    _add(db, "b", cost=5.0, inp=50, out=50, calls=5)
    db.set_session_archived("b", True)
    out = db.sum_session_cost()
    assert out["cost_usd"] == pytest.approx(1.0)
    assert out["calls"] == 1


def test_since_window(db):
    now = time.time()
    _add(db, "old", cost=9.0, inp=90, out=90, calls=9, started_at=now - 10_000)
    _add(db, "new", cost=1.0, inp=10, out=10, calls=1, started_at=now - 5)
    out = db.sum_session_cost(since=now - 100)
    assert out["cost_usd"] == pytest.approx(1.0)  # only the recent session
    assert out["calls"] == 1
