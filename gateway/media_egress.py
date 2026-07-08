"""MEDIA egress for remote terminal backends (direct Modal, docker, ssh…).

The gateway's media delivery reads HOST paths: when a project-bound session
produces an artifact inside its sandbox (``MEDIA:/work/rna/<sid>/fig.png``),
the host has no such file and delivery silently drops it. This module rewrites
such tags before delivery: the file is streamed out of the session's terminal
environment (base64 over the existing exec transport — no new bridge), written
under ``HERMES_HOME/cache/media_egress/``, and the tag is pointed at the host
copy. Local backends and host-visible paths pass through untouched.

Size-capped by ``terminal.media_fetch_max_mb`` (config.yaml): oversized
artifacts stay on the volume and the tag is left as-is, which surfaces the
usual "file not found" warning naming the in-sandbox path.

Scope: any non-local terminal backend whose environment exposes a working
``execute()`` transport (validated on direct Modal; docker/ssh share the same
mechanics). Managed-Modal environments also expose ``execute()`` but this path
is untested there — every failure mode degrades to the pass-through-plus-
warning behavior, never a failed message.

Blocking I/O by design: both call sites run OFF the event loop — the
background result path calls this inside ``run_sync`` (executor thread) and
the post-stream path wraps it in ``_run_in_executor_with_context``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shlex
import time
from pathlib import Path

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Absolute POSIX in-sandbox path after a MEDIA: tag. Extensions mirror
# gateway.run._TOOL_MEDIA_RE (the delivery-side matcher).
_REMOTE_MEDIA_RE = re.compile(
    r"MEDIA:(/\S+\.(?:png|jpe?g|gif|webp|"
    r"mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a|"
    r"flac|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?|"
    r"txt|csv|apk|ipa))",
    re.IGNORECASE,
)

_EGRESS_DIR_NAME = os.path.join("cache", "media_egress")
_EGRESS_GC_SECONDS = 24 * 3600  # opportunistic cleanup of stale fetched copies


def _backend_is_remote() -> bool:
    env_type = (os.environ.get("TERMINAL_ENV", "local").strip().lower() or "local")
    return env_type != "local"


def _egress_dir() -> Path:
    d = get_hermes_home() / _EGRESS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gc_egress_dir(d: Path) -> None:
    now = time.time()
    try:
        for f in d.iterdir():
            try:
                if f.is_file() and now - f.stat().st_mtime > _EGRESS_GC_SECONDS:
                    f.unlink()
            except OSError:
                continue
    except OSError:
        pass


def _max_fetch_bytes() -> int:
    try:
        from gateway.run import _load_gateway_config
        from hermes_cli.config import cfg_get

        mb = cfg_get(_load_gateway_config(), "terminal", "media_fetch_max_mb", default=25)
        mb = float(mb)
    except Exception:
        mb = 25.0
    return int(max(mb, 0) * 1024 * 1024)


def _session_env(task_id: str):
    """The session's live-or-recreated terminal environment (exec transport).

    Reuses the same creation path as the file tools, so a reaped sandbox is
    transparently recreated — with the persistent volume remounted, which is
    exactly where project artifacts live.
    """
    from tools.file_tools import _get_file_ops

    return _get_file_ops(task_id or "default").env


def fetch_remote_file(path: str, task_id: str) -> str | None:
    """Copy *path* out of the session's environment; return the host copy.

    Returns None when the file is missing in the sandbox, oversized, or the
    transport fails — callers leave the original tag in place so the existing
    not-found warning names the real path.
    """
    try:
        env = _session_env(task_id)
        q = shlex.quote(path)

        size_res = env.execute(f"wc -c < {q}", cwd="/", timeout=120)
        if size_res.get("returncode") != 0:
            logger.info("media egress: %s not found in sandbox", path)
            return None
        try:
            size = int(str(size_res.get("output", "")).strip().split()[-1])
        except (ValueError, IndexError):
            return None
        cap = _max_fetch_bytes()
        if size > cap:
            logger.warning(
                "media egress: %s is %.1f MiB, over terminal.media_fetch_max_mb — "
                "leaving it on the volume",
                path, size / (1024 * 1024),
            )
            return None

        b64_res = env.execute(f"base64 < {q}", cwd="/", timeout=600)
        if b64_res.get("returncode") != 0:
            return None
        data = base64.b64decode(
            "".join(str(b64_res.get("output", "")).split()), validate=False
        )
        if not data or len(data) != size:
            logger.warning(
                "media egress: %s transferred %d of %d bytes — dropping",
                path, len(data), size,
            )
            return None

        d = _egress_dir()
        _gc_egress_dir(d)
        digest = hashlib.sha256(f"{task_id}:{path}".encode()).hexdigest()[:10]
        dest = d / f"{digest}-{os.path.basename(path)}"
        dest.write_bytes(data)
        return str(dest)
    except Exception as e:
        logger.warning("media egress failed for %s: %s", path, e)
        return None


def rewrite_remote_media_tags(text: str, task_id: str) -> str:
    """Point MEDIA: tags at host copies of sandbox-side artifacts.

    No-op unless the terminal backend is remote and the text carries a
    MEDIA: tag whose path is absent on the host. Fail-soft throughout: any
    fetch problem leaves the original tag (and its downstream warning) intact.
    """
    if not text or "MEDIA:" not in text or not _backend_is_remote():
        return text
    for match in list(_REMOTE_MEDIA_RE.finditer(text)):
        path = match.group(1).strip().rstrip('",}')
        if not path or os.path.exists(path):
            continue
        local = fetch_remote_file(path, task_id)
        if local:
            text = text.replace(f"MEDIA:{path}", f"MEDIA:{local}")
    return text
