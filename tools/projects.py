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

import contextlib
import logging
import os
import re
import threading
import time

from hermes_constants import get_hermes_home
from tools.environments.base import _load_json_store, _save_json_store

logger = logging.getLogger(__name__)

# Process-local serialization of read-modify-write cycles on projects.json.
# Mutations additionally take the cross-process lockfile below: the registry
# is written by BOTH the messaging gateway (/project new) and the desktop
# backend (dashboard create/rename), which are separate processes.
_lock = threading.RLock()

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Default in-sandbox root for project work on the persistent Modal Volume.
# The actual root comes from the configured volume mount (terminal.modal_volumes
# mount_path); this constant is only the conventional fallback.
DEFAULT_VOLUME_ROOT = "/work"

_LOCK_WAIT_SECONDS = 10.0   # how long to wait on another process's mutation
_LOCK_STALE_SECONDS = 30.0  # a lockfile older than this is from a dead process


@contextlib.contextmanager
def _mutation_lock():
    """Cross-process + in-process serialization for registry mutations.

    A sibling ``projects.json.lock`` is claimed with O_CREAT|O_EXCL (atomic on
    every platform we support, including Windows). Stale locks from crashed
    processes are reclaimed by age. Fail-soft: if the lock cannot be acquired
    within the wait window we proceed anyway (a lost concurrent rename is far
    better than a wedged gateway) — the in-process RLock still holds.
    """
    lock_path = _projects_file().with_suffix(".json.lock")
    with _lock:
        fd = None
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while fd is None:
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > _LOCK_STALE_SECONDS:
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    logger.warning(
                        "projects.json lock held past %.0fs; proceeding without it",
                        _LOCK_WAIT_SECONDS,
                    )
                    break
                time.sleep(0.05)
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)
                with contextlib.suppress(OSError):
                    lock_path.unlink()


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
    with _mutation_lock():
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
    with _mutation_lock():
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


def project_for_cwd(path: str, projects: list[dict] | None = None) -> dict | None:
    """Return the project whose root contains *path* (longest-prefix match).

    A path under ``<project_cwd>`` (the root itself or any descendant) belongs
    to that project. When project roots nest, the deepest (longest) matching
    root wins. Returns None when *path* is under no registered project.

    Pass a pre-loaded *projects* list to match many paths without re-reading the
    registry per call (the gateway does this when tagging a whole session list).
    """
    target = _norm(path)
    if not target:
        return None
    best: dict | None = None
    best_len = -1
    if projects is None:
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


def project_work_path(
    slug: str,
    session_id: str = "",
    child_id: str = "",
    volume_root: str = DEFAULT_VOLUME_ROOT,
) -> str:
    """In-sandbox working path for project work on the persistent volume.

    Convention: ``<volume_root>/<slug>[/<session_id>[/<child_id>]]``. Sessions
    bound to a project work in their own subdirectory; delegated children
    (subagents, kanban tasks) nest one level deeper under their parent session.
    Isolation here is BY PATH, not by sandbox — concurrent sessions and
    subagents share one sandbox, so this convention is what keeps their work
    apart. Always POSIX-joined: the path lives inside the sandbox regardless
    of the host platform.

    Raises ValueError on an empty slug or when *child_id* is given without a
    *session_id* (a child's dir is meaningless outside its parent's).
    """
    slug = (slug or "").strip().strip("/")
    if not slug:
        raise ValueError("project slug is required")
    if child_id and not session_id:
        raise ValueError("child_id requires a session_id")
    root = "/" + (volume_root or DEFAULT_VOLUME_ROOT).strip("/")
    parts = [root, slug]
    if session_id:
        parts.append(str(session_id).strip("/"))
    if child_id:
        parts.append(str(child_id).strip("/"))
    return "/".join(parts)


def resolve_project(
    query: str, projects: list[dict] | None = None
) -> tuple[dict | None, list[dict]]:
    """Resolve a user-supplied name/slug to a project, forgivingly.

    Precedence: exact slug → case-insensitive exact name → unique
    case-insensitive prefix of a slug or name. Returns ``(project, candidates)``:
    exactly one of the two is meaningful — a match with ``[]``, or ``None``
    with the (possibly empty) list of ambiguous candidates so callers can
    show "did you mean …". An empty *query* resolves to nothing.
    """
    query = (query or "").strip()
    if not query:
        return None, []
    if projects is None:
        projects = list_projects()

    for p in projects:
        if p.get("slug") == query:
            return dict(p), []

    lowered = query.lower()
    name_hits = [p for p in projects if str(p.get("name", "")).lower() == lowered]
    if len(name_hits) == 1:
        return dict(name_hits[0]), []
    if name_hits:
        return None, [dict(p) for p in name_hits]

    prefix_hits = [
        p
        for p in projects
        if str(p.get("slug", "")).startswith(lowered)
        or str(p.get("name", "")).lower().startswith(lowered)
    ]
    if len(prefix_hits) == 1:
        return dict(prefix_hits[0]), []
    return None, [dict(p) for p in prefix_hits]
