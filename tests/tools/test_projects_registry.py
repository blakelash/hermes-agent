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
