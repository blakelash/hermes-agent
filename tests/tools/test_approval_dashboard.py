"""Tests for the operator-Dashboard additions to the gateway approval queue.

Covers the non-destructive pending snapshot (``list_pending_approvals``),
resolving a specific entry by ``request_id`` (vs. FIFO / resolve-all), and the
dashboard-change notify that lets the Dashboard refresh without polling. These
assert the queue invariants the Dashboard RPCs depend on, not the regex
detection behavior covered in ``test_approval.py``.
"""

from __future__ import annotations

import threading

import tools.approval as approval


def _make_entry(approval_mod, session_key: str, data: dict):
    """Enqueue a pending approval entry directly and return it.

    Mirrors what ``_await_gateway_decision`` does on enqueue (build entry,
    stamp request_id onto the data, append under the lock) without blocking on
    the wait loop.
    """
    entry = approval_mod._ApprovalEntry(data)
    data["request_id"] = entry.request_id
    with approval_mod._lock:
        approval_mod._gateway_queues.setdefault(session_key, []).append(entry)
    return entry


def _clear(session_key: str):
    with approval._lock:
        approval._gateway_queues.pop(session_key, None)
        approval._gateway_dashboard_cbs.pop(session_key, None)


class TestListPendingApprovals:
    def test_snapshot_includes_request_id_and_data_fields(self):
        sk = "sess-snap-1"
        _clear(sk)
        try:
            _make_entry(approval, sk, {"command": "rm -rf build", "description": "recursive delete"})
            _make_entry(approval, sk, {"command": "git push -f", "description": "git force push"})

            snap = approval.list_pending_approvals(sk)

            assert [e["command"] for e in snap] == ["rm -rf build", "git push -f"]
            assert [e["description"] for e in snap] == ["recursive delete", "git force push"]
            # Every entry carries a stable request_id.
            assert all(e.get("request_id") for e in snap)
            assert len({e["request_id"] for e in snap}) == 2
        finally:
            _clear(sk)

    def test_snapshot_is_non_destructive(self):
        sk = "sess-snap-2"
        _clear(sk)
        try:
            _make_entry(approval, sk, {"command": "mkfs", "description": "format"})

            first = approval.list_pending_approvals(sk)
            second = approval.list_pending_approvals(sk)

            # Reading twice yields the same pending entry — nothing consumed.
            assert len(first) == 1 and len(second) == 1
            with approval._lock:
                assert len(approval._gateway_queues.get(sk, [])) == 1
        finally:
            _clear(sk)

    def test_empty_session_returns_empty_list(self):
        assert approval.list_pending_approvals("does-not-exist") == []

    def test_snapshot_is_a_copy(self):
        sk = "sess-snap-3"
        _clear(sk)
        try:
            _make_entry(approval, sk, {"command": "dd if=/dev/zero", "description": "disk copy"})
            snap = approval.list_pending_approvals(sk)
            snap[0]["command"] = "mutated"
            # Mutating the snapshot must not bleed into the live queue data.
            assert approval.list_pending_approvals(sk)[0]["command"] == "dd if=/dev/zero"
        finally:
            _clear(sk)


class TestResolveByRequestId:
    def test_resolve_targets_specific_entry(self):
        sk = "sess-res-1"
        _clear(sk)
        try:
            e1 = _make_entry(approval, sk, {"command": "a", "description": "d1"})
            e2 = _make_entry(approval, sk, {"command": "b", "description": "d2"})
            e3 = _make_entry(approval, sk, {"command": "c", "description": "d3"})

            # Resolve the MIDDLE entry by id (not the FIFO head).
            n = approval.resolve_gateway_approval(sk, "once", request_id=e2.request_id)

            assert n == 1
            assert e2.result == "once" and e2.event.is_set()
            # Head + tail remain pending and untouched.
            assert e1.result is None and not e1.event.is_set()
            assert e3.result is None and not e3.event.is_set()
            remaining = {x["request_id"] for x in approval.list_pending_approvals(sk)}
            assert remaining == {e1.request_id, e3.request_id}
        finally:
            _clear(sk)

    def test_unknown_request_id_resolves_nothing(self):
        sk = "sess-res-2"
        _clear(sk)
        try:
            e1 = _make_entry(approval, sk, {"command": "a", "description": "d1"})
            n = approval.resolve_gateway_approval(sk, "deny", request_id="nonexistent")
            assert n == 0
            assert e1.result is None and not e1.event.is_set()
            assert len(approval.list_pending_approvals(sk)) == 1
        finally:
            _clear(sk)

    def test_fifo_behavior_preserved_without_request_id(self):
        sk = "sess-res-3"
        _clear(sk)
        try:
            e1 = _make_entry(approval, sk, {"command": "a", "description": "d1"})
            e2 = _make_entry(approval, sk, {"command": "b", "description": "d2"})

            # No request_id, no resolve_all → oldest only.
            n = approval.resolve_gateway_approval(sk, "session")
            assert n == 1
            assert e1.result == "session" and e1.event.is_set()
            assert e2.result is None and not e2.event.is_set()
        finally:
            _clear(sk)

    def test_resolve_all_preserved(self):
        sk = "sess-res-4"
        _clear(sk)
        try:
            e1 = _make_entry(approval, sk, {"command": "a", "description": "d1"})
            e2 = _make_entry(approval, sk, {"command": "b", "description": "d2"})

            n = approval.resolve_gateway_approval(sk, "always", resolve_all=True)
            assert n == 2
            assert e1.event.is_set() and e2.event.is_set()
            assert approval.list_pending_approvals(sk) == []
        finally:
            _clear(sk)


class TestDashboardNotify:
    def test_resolve_fires_change_callback_with_remaining_set(self):
        sk = "sess-cb-1"
        _clear(sk)
        seen: list[dict] = []
        try:
            approval.register_gateway_dashboard_notify(sk, lambda p: seen.append(p))
            e1 = _make_entry(approval, sk, {"command": "a", "description": "d1"})
            e2 = _make_entry(approval, sk, {"command": "b", "description": "d2"})

            approval.resolve_gateway_approval(sk, "once", request_id=e1.request_id)

            assert len(seen) == 1
            assert seen[-1]["needs_count"] == 1
            assert seen[-1]["request_ids"] == [e2.request_id]
        finally:
            _clear(sk)

    def test_callback_not_fired_when_nothing_resolved(self):
        sk = "sess-cb-2"
        _clear(sk)
        seen: list[dict] = []
        try:
            approval.register_gateway_dashboard_notify(sk, lambda p: seen.append(p))
            approval.resolve_gateway_approval(sk, "deny", request_id="missing")
            assert seen == []
        finally:
            _clear(sk)

    def test_callback_errors_do_not_break_resolution(self):
        sk = "sess-cb-3"
        _clear(sk)
        try:
            def _boom(_payload):
                raise RuntimeError("observer down")

            approval.register_gateway_dashboard_notify(sk, _boom)
            e1 = _make_entry(approval, sk, {"command": "a", "description": "d1"})

            # A throwing observer must not prevent the waiter from unblocking.
            n = approval.resolve_gateway_approval(sk, "deny", request_id=e1.request_id)
            assert n == 1
            assert e1.result == "deny" and e1.event.is_set()
        finally:
            _clear(sk)

    def test_unregister_removes_callback(self):
        sk = "sess-cb-4"
        _clear(sk)
        seen: list[dict] = []
        try:
            approval.register_gateway_dashboard_notify(sk, lambda p: seen.append(p))
            approval.unregister_gateway_dashboard_notify(sk)
            e1 = _make_entry(approval, sk, {"command": "a", "description": "d1"})
            approval.resolve_gateway_approval(sk, "deny", request_id=e1.request_id)
            assert seen == []
        finally:
            _clear(sk)


def test_request_id_is_stable_across_snapshots():
    """A pending entry keeps the same id between snapshot reads — the contract
    the Dashboard relies on to resolve the exact request the operator saw."""
    sk = "sess-stable"
    _clear(sk)
    try:
        e1 = _make_entry(approval, sk, {"command": "a", "description": "d1"})
        first = approval.list_pending_approvals(sk)[0]["request_id"]
        second = approval.list_pending_approvals(sk)[0]["request_id"]
        assert first == second == e1.request_id
    finally:
        _clear(sk)


class TestListAllPendingApprovals:
    def test_spans_all_queues_and_tags_session_key(self):
        ka, kb = "sess-all-a", "sess-all-b"
        _clear(ka)
        _clear(kb)
        try:
            ea = _make_entry(approval, ka, {"command": "a", "description": "da"})
            eb1 = _make_entry(approval, kb, {"command": "b1", "description": "db1"})
            eb2 = _make_entry(approval, kb, {"command": "b2", "description": "db2"})

            snap = approval.list_all_pending_approvals()
            mine = [s for s in snap if s.get("session_key") in {ka, kb}]
            # Every entry carries its owning session_key + request_id.
            by_key: dict[str, list] = {}
            for s in mine:
                assert s["request_id"]
                by_key.setdefault(s["session_key"], []).append(s["request_id"])
            assert by_key[ka] == [ea.request_id]
            assert by_key[kb] == [eb1.request_id, eb2.request_id]
        finally:
            _clear(ka)
            _clear(kb)

    def test_non_destructive(self):
        sk = "sess-all-nd"
        _clear(sk)
        try:
            _make_entry(approval, sk, {"command": "x", "description": "d"})
            approval.list_all_pending_approvals()
            # Reading does not consume the queue.
            assert len(approval.list_pending_approvals(sk)) == 1
        finally:
            _clear(sk)
