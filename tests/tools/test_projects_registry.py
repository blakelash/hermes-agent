"""Tests for the project registry (tools/projects.py).

The registry is a JSON-backed list of named, profile-scoped projects under the
active Hermes home (isolated to a per-test tempdir by the autouse fixture).
Each project owns a managed dir at ``get_hermes_home()/projects/<slug>``. These
assert the invariants the Dashboard depends on: managed-dir creation, unique
stable slugs, rename keeps slug/cwd, longest-prefix project_for_cwd, and
fail-soft on a corrupt store.
"""

from __future__ import annotations

import json
import os
import time

import pytest

import tools.projects as projects
from hermes_constants import get_hermes_home


def test_create_makes_managed_dir_and_persists():
    p = projects.create_project("My Project")
    assert p["slug"] == "my-project"
    assert p["name"] == "My Project"
    # cwd is the managed dir under the (isolated) Hermes home, and it exists.
    expected = os.path.realpath(get_hermes_home() / "projects" / "my-project")
    assert os.path.realpath(p["cwd"]) == expected
    assert os.path.isdir(p["cwd"])

    store = get_hermes_home() / "projects.json"
    assert store.is_file()
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["projects"][0]["slug"] == "my-project"


def test_list_projects():
    projects.create_project("Alpha")
    projects.create_project("Beta")
    slugs = [p["slug"] for p in projects.list_projects()]
    assert slugs == ["alpha", "beta"]


def test_slug_dedup():
    p1 = projects.create_project("Same Name")
    p2 = projects.create_project("Same Name")
    assert p1["slug"] == "same-name"
    assert p2["slug"] == "same-name-2"
    assert p1["cwd"] != p2["cwd"]
    assert os.path.isdir(p1["cwd"]) and os.path.isdir(p2["cwd"])


def test_get_project():
    created = projects.create_project("Proj")
    got = projects.get_project(created["slug"])
    assert got is not None and got["slug"] == created["slug"]
    assert projects.get_project("nope") is None


def test_rename_keeps_slug_and_cwd():
    created = projects.create_project("Old Name")
    renamed = projects.rename_project(created["slug"], "New Name")
    assert renamed["slug"] == created["slug"]
    assert renamed["cwd"] == created["cwd"]
    assert renamed["name"] == "New Name"
    assert projects.get_project(created["slug"])["name"] == "New Name"


def test_create_rejects_empty_name():
    with pytest.raises(ValueError):
        projects.create_project("   ")


def test_rename_unknown_slug_raises():
    with pytest.raises(ValueError):
        projects.rename_project("ghost", "Whatever")


def test_slugify_punctuation_only_name():
    p = projects.create_project("!!!")
    assert p["slug"] == "project"


def test_project_for_cwd_matches_root_and_descendants():
    p = projects.create_project("Demo")
    root = p["cwd"]
    assert projects.project_for_cwd(root)["slug"] == "demo"
    sub = os.path.join(root, "session-123", "src")
    assert projects.project_for_cwd(sub)["slug"] == "demo"
    assert projects.project_for_cwd("/definitely/not/a/project") is None


def test_project_for_cwd_longest_prefix_wins():
    # A nested project under another project's tree: the deepest (longest)
    # matching root must win.
    outer = projects.create_project("Outer")
    nested_dir = os.path.join(outer["cwd"], "nested")
    os.makedirs(nested_dir, exist_ok=True)
    store = get_hermes_home() / "projects.json"
    data = json.loads(store.read_text(encoding="utf-8"))
    data["projects"].append({"slug": "nested", "name": "Nested", "cwd": nested_dir})
    store.write_text(json.dumps(data), encoding="utf-8")

    deep = os.path.join(nested_dir, "x")
    assert projects.project_for_cwd(deep)["slug"] == "nested"
    assert projects.project_for_cwd(os.path.join(outer["cwd"], "other"))["slug"] == "outer"


def test_corrupt_store_treated_as_empty():
    store = get_hermes_home() / "projects.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{ not json", encoding="utf-8")
    assert projects.list_projects() == []
    p = projects.create_project("Recover")
    assert p["slug"] == "recover"


# ──────────────────────────────────────────────────────────────────────────
# project_work_path — the in-sandbox volume path convention
# ──────────────────────────────────────────────────────────────────────────

def test_project_work_path_levels():
    assert projects.project_work_path("rna") == "/work/rna"
    assert projects.project_work_path("rna", "sess1") == "/work/rna/sess1"
    assert (
        projects.project_work_path("rna", "sess1", "child2")
        == "/work/rna/sess1/child2"
    )


def test_project_work_path_custom_volume_root():
    assert projects.project_work_path("rna", "s", volume_root="/data/") == "/data/rna/s"
    # Falls back to the default root when blank.
    assert projects.project_work_path("rna", volume_root="") == "/work/rna"


def test_project_work_path_is_posix_regardless_of_host():
    # Sandbox paths never use the host separator.
    path = projects.project_work_path("rna", "sess1", "child2")
    assert "\\" not in path and path.startswith("/")


def test_project_work_path_rejects_bad_args():
    with pytest.raises(ValueError):
        projects.project_work_path("")
    with pytest.raises(ValueError):
        projects.project_work_path("rna", "", "child-without-session")


# ──────────────────────────────────────────────────────────────────────────
# resolve_project — forgiving name/slug resolution for /project set
# ──────────────────────────────────────────────────────────────────────────

def test_resolve_exact_slug_wins():
    projects.create_project("RNA Analysis")
    match, candidates = projects.resolve_project("rna-analysis")
    assert match["slug"] == "rna-analysis" and candidates == []


def test_resolve_name_case_insensitive():
    projects.create_project("RNA Analysis")
    match, _ = projects.resolve_project("rna analysis")
    assert match["slug"] == "rna-analysis"


def test_resolve_unique_prefix():
    projects.create_project("RNA Analysis")
    projects.create_project("Proteomics")
    match, _ = projects.resolve_project("prot")
    assert match["slug"] == "proteomics"


def test_resolve_ambiguous_prefix_returns_candidates():
    projects.create_project("RNA Analysis")
    projects.create_project("RNA Structures")
    match, candidates = projects.resolve_project("rna")
    assert match is None
    assert {c["slug"] for c in candidates} == {"rna-analysis", "rna-structures"}


def test_resolve_no_match_and_empty_query():
    projects.create_project("Alpha")
    assert projects.resolve_project("zzz") == (None, [])
    assert projects.resolve_project("") == (None, [])


# ──────────────────────────────────────────────────────────────────────────
# Cross-process mutation lock
# ──────────────────────────────────────────────────────────────────────────

def test_mutation_removes_its_lockfile():
    projects.create_project("Alpha")
    assert not (get_hermes_home() / "projects.json.lock").exists()


def test_held_foreign_lock_delays_but_never_wedges(monkeypatch):
    """A fresh lockfile from another process delays the mutation; the
    fail-soft timeout guarantees the gateway can never deadlock on it."""
    lock = get_hermes_home() / "projects.json.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("other-pid", encoding="utf-8")
    monkeypatch.setattr(projects, "_LOCK_WAIT_SECONDS", 0.2)
    p = projects.create_project("Alpha")  # proceeds after the wait window
    assert p["slug"] == "alpha"
    lock.unlink(missing_ok=True)


def test_stale_lock_is_reclaimed():
    lock = get_hermes_home() / "projects.json.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("dead-pid", encoding="utf-8")
    stale = time.time() - 120
    os.utime(lock, (stale, stale))
    p = projects.create_project("Alpha")
    assert p["slug"] == "alpha"
    assert not lock.exists()


def test_concurrent_creates_lose_no_update():
    """Two racing creators (threads simulating two processes' interleaved
    read-modify-write) must both land in the store."""
    import threading

    errs = []

    def make(name):
        try:
            projects.create_project(name)
        except Exception as e:  # pragma: no cover - failure detail
            errs.append(e)

    threads = [threading.Thread(target=make, args=(f"proj {i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs
    assert len(projects.list_projects()) == 8
