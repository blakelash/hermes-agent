"""Project registry — named, profile-scoped workspace roots for the Dashboard.

A *project* is a human-named workspace that the operator Dashboard groups
sessions under: several chat sessions share one project, each working in its own
subdirectory (``<project_cwd>/<session_id>``) while the project root is the
shared containment boundary. This module is the single source of truth for the
registry; the gateway exposes it over the ``projects.*`` JSON-RPC methods and
joins it with live session state for the global ``dashboard.snapshot``.

Each project owns a managed directory ``get_hermes_home()/projects/<slug>``
(created on registration) — projects are not arbitrary pre-existing folders.

Persistence mirrors the modal snapshot store (``tools/environments/modal.py``):
a single JSON file at ``get_hermes_home()/projects.json`` loaded/saved via the
shared ``_load_json_store`` / ``_save_json_store`` helpers. The path is resolved
at call time (never snapshotted at import) so it tracks the active profile's
``HERMES_HOME`` — the rule the rest of the codebase follows via
:func:`get_hermes_home`.

A registry entry: ``{"slug": str, "name": str, "cwd": str (absolute)}``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time

from hermes_constants import get_hermes_home
from tools.environments.base import _load_json_store, _save_json_store

logger = logging.getLogger(__name__)

# Process-local serialization of read-modify-write cycles on projects.json.
# Projects are only mutated from the gateway process, so a single in-process
# lock is sufficient (no cross-process file lock needed).
_lock = threading.RLock()

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _projects_file():
    """Path to the registry JSON under the active Hermes home (per-call)."""
    return get_hermes_home() / "projects.json"


def _projects_root():
    """Directory that holds every project's managed working tree."""
    return get_hermes_home() / "projects"


def _slugify(name: str) -> str:
    """Filesystem/url-safe slug from a name; never empty."""
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "project"


def _unique_slug(base: str, existing: set[str]) -> str:
    """Return *base*, or ``base-2``/``base-3``/… on collision."""
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _read() -> list[dict]:
    """Load the project list. Returns [] when absent/corrupt (fail-soft)."""
    data = _load_json_store(_projects_file())
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list):
        return []
    return [p for p in projects if isinstance(p, dict)]


def _write(projects: list[dict]) -> None:
    """Persist the project list. Caller must hold ``_lock``."""
    _save_json_store(_projects_file(), {"projects": projects, "updated_at": time.time()})


def list_projects() -> list[dict]:
    """Non-destructive snapshot of all registered projects (registry order)."""
    with _lock:
        return [dict(p) for p in _read()]


def get_project(slug: str) -> dict | None:
    """Return the project with *slug*, or None if not registered."""
    if not slug:
        return None
    with _lock:
        for p in _read():
            if p.get("slug") == slug:
                return dict(p)
    return None


def create_project(name: str) -> dict:
    """Register a project named *name*, creating its managed working dir.

    The slug is derived from *name* and de-duplicated against existing slugs;
    the working directory ``get_hermes_home()/projects/<slug>`` is created.
    Returns the stored entry ``{slug, name, cwd}``.

    Raises ValueError on an empty name.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("project name is required")
    with _lock:
        projects = _read()
        existing = {str(p.get("slug", "")) for p in projects}
        slug = _unique_slug(_slugify(name), existing)
        cwd = os.path.abspath(_projects_root() / slug)
        os.makedirs(cwd, exist_ok=True)
        entry = {"slug": slug, "name": name, "cwd": cwd}
        projects.append(entry)
        _write(projects)
        return dict(entry)


def rename_project(slug: str, name: str) -> dict:
    """Change a project's display name. Slug and cwd are unchanged.

    Raises ValueError on empty name or unknown slug.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("project name is required")
    with _lock:
        projects = _read()
        for p in projects:
            if p.get("slug") == slug:
                p["name"] = name
                _write(projects)
                return dict(p)
    raise ValueError(f"unknown project slug: {slug}")


def _norm(path: str) -> str:
    """Absolute, real (symlink-resolved) form of *path* for prefix matching."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path or ""))))


def project_for_cwd(path: str) -> dict | None:
    """Return the project whose root contains *path* (longest-prefix match).

    A path under ``<project_cwd>`` (the root itself or any descendant) belongs
    to that project. When project roots nest, the deepest (longest) matching
    root wins. Returns None when *path* is under no registered project.
    """
    target = _norm(path)
    if not target:
        return None
    best: dict | None = None
    best_len = -1
    with _lock:
        projects = _read()
    for p in projects:
        root = _norm(p.get("cwd", ""))
        if not root:
            continue
        if target == root or target.startswith(root + os.sep):
            if len(root) > best_len:
                best, best_len = dict(p), len(root)
    return best
