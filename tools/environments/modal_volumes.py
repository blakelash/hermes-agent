"""Shared parsing/normalization for Modal Volume mount configuration.

Modal Volumes give Hermes sandboxes a *persistent* filesystem that survives
sandbox teardown and can be mounted into many sandboxes at once — the storage
layer for long-running scientific work (datasets, checkpoints, figures).

This module is intentionally dependency-free (stdlib only) so it can be imported
cheaply from the terminal tool, the Modal backend, and the system-prompt builder
without pulling in the Modal SDK or the heavier environment base classes.

Canonical normalized shape (one dict per mounted volume)::

    {
        "name": "hermes-science",   # Modal Volume name (modal.Volume.from_name)
        "mount_path": "/work",      # absolute path inside the sandbox
        "create_if_missing": True,  # create the volume on first use
        "read_only": False,         # mount read-only
    }

Accepted raw inputs (from ``TERMINAL_MODAL_VOLUMES`` or config.yaml):
  - JSON string of any of the below
  - list of dicts: ``[{"name": "...", "mount_path": "/work", ...}]``
    (``path`` is accepted as an alias for ``mount_path``)
  - list of ``"name:/mount/path"`` strings
  - dict shorthand mapping mount path to volume name: ``{"/work": "hermes-science"}``
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_entry(entry: Any) -> dict | None:
    """Coerce a single raw entry into the canonical dict, or None if invalid."""
    if isinstance(entry, str):
        # "name:/mount/path" shorthand. rsplit so volume names may contain ':'
        # only if a path follows; require exactly one path component.
        text = entry.strip()
        if not text:
            return None
        if ":" not in text:
            logger.warning(
                "Ignoring Modal volume entry %r: expected 'name:/mount/path'", entry
            )
            return None
        name, mount_path = text.split(":", 1)
        name, mount_path = name.strip(), mount_path.strip()
    elif isinstance(entry, dict):
        name = str(entry.get("name", "")).strip()
        mount_path = str(entry.get("mount_path", entry.get("path", ""))).strip()
    else:
        logger.warning("Ignoring Modal volume entry of unsupported type: %r", entry)
        return None

    if not name or not mount_path:
        logger.warning(
            "Ignoring Modal volume entry %r: both 'name' and 'mount_path' are required",
            entry,
        )
        return None
    if not mount_path.startswith("/"):
        logger.warning(
            "Ignoring Modal volume %r: mount_path %r must be absolute", name, mount_path
        )
        return None

    create_if_missing = True
    read_only = False
    if isinstance(entry, dict):
        create_if_missing = bool(entry.get("create_if_missing", True))
        read_only = bool(entry.get("read_only", False))

    return {
        "name": name,
        "mount_path": mount_path.rstrip("/") or "/",
        "create_if_missing": create_if_missing,
        "read_only": read_only,
    }


def normalize_modal_volumes(raw: Any) -> list[dict]:
    """Normalize any accepted volume config into a list of canonical dicts.

    Lenient by design: invalid entries are skipped with a warning rather than
    raising, so a malformed volume config never makes the terminal unusable.
    Later entries win on duplicate mount paths.
    """
    if raw is None or raw == "":
        return []

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            # Allow a bare "name:/mount/path" string without JSON quoting.
            raw = [raw]

    entries: list[Any]
    if isinstance(raw, dict):
        # {mount_path: name} shorthand
        entries = [{"mount_path": k, "name": v} for k, v in raw.items()]
    elif isinstance(raw, list):
        entries = raw
    else:
        logger.warning("Ignoring Modal volume config of unsupported type: %r", type(raw))
        return []

    by_mount: dict[str, dict] = {}
    for entry in entries:
        normalized = _normalize_entry(entry)
        if normalized is not None:
            by_mount[normalized["mount_path"]] = normalized
    return list(by_mount.values())


def parse_modal_volumes_env(value: str | None) -> list[dict]:
    """Parse the ``TERMINAL_MODAL_VOLUMES`` env var value into canonical dicts."""
    return normalize_modal_volumes(value)


def describe_modal_volumes(volumes: list[dict]) -> str:
    """One-line-per-volume human/agent-facing description, or '' if none."""
    if not volumes:
        return ""
    lines = []
    for vol in volumes:
        flag = " (read-only)" if vol.get("read_only") else ""
        lines.append(f"{vol['mount_path']} -> Modal Volume '{vol['name']}'{flag}")
    return "\n".join(lines)
