"""Project working-directory binding for messaging-gateway sessions.

A gateway session bound to a project (``SessionEntry.project_slug``) works in
a per-session directory derived from one path convention
(:func:`tools.projects.project_work_path`):

- **Remote terminal backends** (modal, docker, ssh, …): the directory lives
  INSIDE the sandbox at ``<volume_root>/<slug>/<session_id>`` — on Modal that
  is the persistent Volume mount (``terminal.modal_volumes``), so the work
  survives sandbox teardown and is shared across the project's sessions.
  Nothing host-side is created; the host never sees these files except at
  delivery time (MEDIA egress).
- **Local backend**: the directory is the project's managed host dir
  ``<project_cwd>/<session_id>``.

The binding is applied as a *pinned* per-task cwd override
(``register_task_env_overrides(session_id, {"cwd": ..., "pin_cwd": True})``).
Pinning matters because concurrent gateway sessions share one sandbox
("default" container): the shared environment's live ``env.cwd`` is mutated by
every session's ``cd``, so a bound session must resolve its working directory
from its own raw-keyed override, not from the shared tracker (see
``terminal_tool._resolve_command_cwd`` / ``file_tools._authoritative_workspace_root``).

Prompt-cache safety: the system-prompt hint built here is baked into a
session's prompt at its first turn and never mutated afterwards — continuing
sessions reuse the stored prompt verbatim (``conversation_loop``), so a
mid-session rebind (agent ``project_bind`` tool) changes the working dir and
the NEXT session's prompt, never the current conversation's bytes.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def backend_is_local() -> bool:
    """True when the configured terminal backend runs on the host."""
    return (os.environ.get("TERMINAL_ENV", "local").strip().lower() or "local") == "local"


def resolve_volume_root() -> str:
    """Sandbox-side root for project work.

    The first writable configured Modal volume mount wins; the conventional
    ``/work`` is the fallback so the path shape is stable even before a volume
    is configured (files then live on the sandbox FS / task snapshot instead
    of a durable volume — degraded persistence, same layout).
    """
    from tools.environments.modal_volumes import parse_modal_volumes_env
    from tools.projects import DEFAULT_VOLUME_ROOT

    try:
        volumes = parse_modal_volumes_env(os.environ.get("TERMINAL_MODAL_VOLUMES"))
    except Exception:
        volumes = []
    for vol in volumes:
        if not vol.get("read_only"):
            return vol["mount_path"]
    return DEFAULT_VOLUME_ROOT


def session_workdir(slug: str, session_id: str) -> str | None:
    """Backend-aware per-session project working directory, or None.

    None means the binding cannot produce a usable directory (unknown project
    on a local backend, missing ids) — callers treat that as "unbound" rather
    than failing the message.
    """
    from tools.projects import get_project, project_work_path

    slug = (slug or "").strip()
    if not slug or not session_id:
        return None
    if backend_is_local():
        project = get_project(slug)
        root = (project or {}).get("cwd") or ""
        if not root:
            logger.warning("project %r not in registry; session stays unbound", slug)
            return None
        return os.path.join(root, session_id)
    try:
        return project_work_path(slug, session_id, volume_root=resolve_volume_root())
    except ValueError:
        return None


def register_session_workdir(session_id: str, slug: str) -> str | None:
    """Idempotently pin *session_id*'s terminal/file cwd to its project dir.

    Returns the working directory, or None when the binding is unusable.
    Safe to call on every message: registration is a dict write, and the
    host-side makedirs only runs for the local backend. Remote directories
    are created lazily by the terminal layer on first use
    (``terminal_tool._ensure_pinned_cwd``) so binding a session never spins
    up a sandbox by itself.
    """
    workdir = session_workdir(slug, session_id)
    if not workdir:
        return None
    if backend_is_local():
        try:
            os.makedirs(workdir, exist_ok=True)
        except OSError as e:
            logger.warning("cannot create project workdir %s: %s", workdir, e)
            return None

    from tools.terminal_tool import register_task_env_overrides, resolve_task_overrides

    existing = resolve_task_overrides(session_id)
    if existing.get("cwd") != workdir or not existing.get("pin_cwd"):
        register_task_env_overrides(session_id, {"cwd": workdir, "pin_cwd": True})
    return workdir


def release_session_workdir(session_id: str) -> None:
    """Drop a session's pinned override (rebind/unbind housekeeping).

    Only clears overrides this module registered (``pin_cwd``) so RL/benchmark
    isolation overrides registered under the same id are never touched.
    """
    from tools.terminal_tool import (
        clear_task_env_overrides,
        resolve_task_overrides,
    )

    if not session_id:
        return
    if resolve_task_overrides(session_id).get("pin_cwd"):
        clear_task_env_overrides(session_id)


def project_prompt_hint(slug: str, session_id: str) -> str:
    """System-prompt paragraph for a project-bound session ('' when unusable).

    Baked into the prompt at the session's first turn; byte-stability across
    the conversation is guaranteed by the stored-prompt reuse path, not by
    this function, so using the live registry name here is safe.
    """
    from tools.projects import get_project

    workdir = session_workdir(slug, session_id)
    if not workdir:
        return ""
    project = get_project(slug)
    name = (project or {}).get("name") or slug
    lines = [
        f'This conversation is bound to the project "{name}" (slug: {slug}).',
        f"Your working directory for this session is {workdir} — keep this "
        "session's files under it so the project stays organized.",
    ]
    if not backend_is_local():
        lines.append(
            "That directory is inside your terminal environment on the "
            "project's persistent storage: files there survive environment "
            "teardown and are shared with the project's other sessions "
            f"(the project root is {workdir.rsplit('/', 2)[0]}/{slug})."
        )
    return "\n".join(lines)
