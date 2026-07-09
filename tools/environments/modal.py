"""Modal cloud execution environment using the native Modal SDK directly.

Uses ``Sandbox.create()`` + ``Sandbox.exec()`` instead of the older runtime
wrapper, while preserving Hermes' persistent snapshot behavior across sessions.

Modal calls use the **blocking** SDK surface (not the ``.aio()`` coroutines)
and are funneled through a dedicated, event-loop-free thread pool. Modal's SDK
is sync-over-async via ``synchronicity``, which manages its own internal event
loop; driving Modal's ``.aio()`` coroutines on a *foreign* loop breaks
modal>=1.3's sandbox command-router exec path (``RuntimeError: no running event
loop`` deep in grpclib). Calling the blocking API from threads with no running
loop lets synchronicity own its loop and works reliably.
"""

import base64
import concurrent.futures
import io
import logging
import os
import shlex
import tarfile
import threading
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from tools.environments.base import (
    BaseEnvironment,
    _ThreadedProcessHandle,
    _load_json_store,
    _save_json_store,
)
from tools.environments.file_sync import (
    FileSyncManager,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

logger = logging.getLogger(__name__)

_SNAPSHOT_STORE = get_hermes_home() / "modal_snapshots.json"
_DIRECT_SNAPSHOT_NAMESPACE = "direct"

# Fallback max wall-clock lifetime (seconds) for a sandbox when the caller
# doesn't pass one. This is Modal's own hard cap: Modal terminates the sandbox
# once it's exceeded, regardless of what Hermes thinks. It only bites sandboxes
# that Hermes deliberately keeps alive (a live background process refreshes the
# idle-reaper's activity clock, so the reaper skips them) — an idle sandbox is
# still torn down early by terminal.lifetime_seconds. Keep this generous so a
# long-running task isn't guillotined mid-run; recreate-on-death (below) covers
# the case where it dies anyway.
_DEFAULT_SANDBOX_TIMEOUT = 21_600  # 6 hours

# Substrings that mark a Modal error as "the sandbox is gone" (reaped after
# exceeding its lifetime cap, evicted, or otherwise terminated server-side).
# Matched case-insensitively against the exception text because the concrete
# Modal exception types have shifted across SDK versions.
_SANDBOX_DEAD_MARKERS = (
    "has already shut down",
    "has terminated",
    "sandbox has finished",
    "not found",
)


def _is_sandbox_dead_error(exc: BaseException) -> bool:
    """Return True if *exc* indicates the sandbox no longer exists server-side."""
    message = str(exc).lower()
    return any(marker in message for marker in _SANDBOX_DEAD_MARKERS)


def _collect_passthrough_env() -> dict[str, str]:
    """Return host env vars that are allowlisted for sandbox passthrough.

    Reuses the session-scoped allowlist from :mod:`tools.env_passthrough`
    (skill ``required_environment_variables`` + the ``terminal.env_passthrough``
    config list). Only names present in the host environment are forwarded.

    The allowlist already refuses Hermes-managed provider credentials (per
    ``_HERMES_PROVIDER_ENV_BLOCKLIST`` / GHSA-rhgp-j443-p4rf), so this never
    ships the agent's own API keys to the sandbox; third-party keys such as
    ``NOTION_API_KEY`` pass through normally once allowlisted.
    """
    try:
        from tools.env_passthrough import get_all_passthrough
    except Exception as e:
        logger.debug("Modal: could not load env passthrough allowlist: %s", e)
        return {}

    forwarded: dict[str, str] = {}
    for name in get_all_passthrough():
        value = os.environ.get(name)
        if value is not None:
            forwarded[name] = value
    return forwarded


def _load_snapshots() -> dict:
    return _load_json_store(_SNAPSHOT_STORE)


def _save_snapshots(data: dict) -> None:
    _save_json_store(_SNAPSHOT_STORE, data)


def _direct_snapshot_key(task_id: str) -> str:
    return f"{_DIRECT_SNAPSHOT_NAMESPACE}:{task_id}"


def _get_snapshot_restore_candidate(task_id: str) -> tuple[str | None, bool]:
    snapshots = _load_snapshots()
    namespaced_key = _direct_snapshot_key(task_id)
    snapshot_id = snapshots.get(namespaced_key)
    if isinstance(snapshot_id, str) and snapshot_id:
        return snapshot_id, False
    legacy_snapshot_id = snapshots.get(task_id)
    if isinstance(legacy_snapshot_id, str) and legacy_snapshot_id:
        return legacy_snapshot_id, True
    return None, False


def _store_direct_snapshot(task_id: str, snapshot_id: str) -> None:
    snapshots = _load_snapshots()
    snapshots[_direct_snapshot_key(task_id)] = snapshot_id
    snapshots.pop(task_id, None)
    _save_snapshots(snapshots)


def _delete_direct_snapshot(task_id: str, snapshot_id: str | None = None) -> None:
    snapshots = _load_snapshots()
    updated = False
    for key in (_direct_snapshot_key(task_id), task_id):
        value = snapshots.get(key)
        if value is None:
            continue
        if snapshot_id is None or value == snapshot_id:
            snapshots.pop(key, None)
            updated = True
    if updated:
        _save_snapshots(snapshots)


def _ensure_modal_sdk() -> None:
    """Lazy-install modal on demand. Idempotent — fast no-op once installed."""
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("terminal.modal", prompt=False)
    except ImportError:
        pass
    except Exception as e:
        raise ImportError(str(e))


def _resolve_modal_image(image_spec: Any) -> Any:
    """Convert registry references or snapshot ids into Modal image objects.

    Includes add_python support for ubuntu/debian images (absorbed from PR 4511).
    """
    _ensure_modal_sdk()
    import modal as _modal

    if not isinstance(image_spec, str):
        return image_spec

    if image_spec.startswith("im-"):
        return _modal.Image.from_id(image_spec)

    # PR 4511: add python to ubuntu/debian images that don't have it
    lower = image_spec.lower()
    add_python = any(base in lower for base in ("ubuntu", "debian"))

    setup_commands = [
        "RUN rm -rf /usr/local/lib/python*/site-packages/pip* 2>/dev/null; "
        "python -m ensurepip --upgrade --default-pip 2>/dev/null || true",
    ]
    if add_python:
        setup_commands.insert(0,
            "RUN apt-get update -qq && apt-get install -y -qq python3 python3-venv > /dev/null 2>&1 || true"
        )

    return _modal.Image.from_registry(
        image_spec,
        setup_dockerfile_commands=setup_commands,
    )


class _SyncWorker:
    """Dedicated, event-loop-free thread pool for blocking Modal SDK calls.

    Why not just call Modal inline? Two reasons:

    1. Modal's ``synchronicity`` blocking API must NOT be invoked from a thread
       that already has a running asyncio event loop. Routing every Modal call
       onto pool threads (which never run a loop) guarantees that.
    2. The previous implementation drove Modal's ``.aio()`` coroutines on a
       hand-rolled event loop, which broke modal>=1.3's command-router exec
       (``RuntimeError: no running event loop``). Using the blocking API on
       loop-free threads sidesteps that entirely.

    Several workers (not one) so an in-flight ``exec`` and a concurrent
    ``terminate`` (interrupt) don't serialize behind each other. Modal handles
    concurrent blocking calls from multiple threads fine.
    """

    def __init__(self):
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="modal-env"
        )

    def run(self, fn, timeout=600):
        return self._executor.submit(fn).result(timeout=timeout)

    def stop(self):
        self._executor.shutdown(wait=False, cancel_futures=True)


class ModalEnvironment(BaseEnvironment):
    """Modal cloud execution via native Modal sandboxes.

    Spawn-per-call via _ThreadedProcessHandle wrapping blocking SDK calls.
    cancel_fn wired to sandbox.terminate for interrupt support.
    """

    _stdin_mode = "heredoc"
    _snapshot_timeout = 60  # Modal cold starts can be slow

    def __init__(
        self,
        image: str,
        cwd: str = "/root",
        timeout: int = 60,
        modal_sandbox_kwargs: Optional[dict[str, Any]] = None,
        persistent_filesystem: bool = True,
        task_id: str = "default",
        modal_volumes: Optional[list[dict]] = None,
    ):
        super().__init__(cwd=cwd, timeout=timeout)

        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._sandbox = None
        self._app = None
        self._worker = _SyncWorker()
        self._sync_manager: FileSyncManager | None = None  # initialized after sandbox creation
        self._modal_volumes = list(modal_volumes or [])

        # Recreate-on-death bookkeeping. When Modal reaps a sandbox out from
        # under us (e.g. it exceeded its lifetime cap while a background job kept
        # it alive), the next exec/file op transparently rebuilds it instead of
        # bricking every subsequent terminal/file call. The generation counter
        # lets concurrent callers that grabbed the same dead handle skip a
        # redundant rebuild, and _recreating guards against re-entrant rebuilds
        # from the resync that a rebuild itself performs.
        self._sandbox_generation = 0
        self._recreate_lock = threading.Lock()
        self._recreating = False

        self._image = image
        self._sandbox_kwargs = dict(modal_sandbox_kwargs or {})

        restored_snapshot_id = None
        restored_from_legacy_key = False
        if self._persistent:
            restored_snapshot_id, restored_from_legacy_key = _get_snapshot_restore_candidate(
                self._task_id
            )
            if restored_snapshot_id:
                logger.info("Modal: restoring from snapshot %s", restored_snapshot_id[:20])
        self._restored_snapshot_id = restored_snapshot_id
        self._restored_from_legacy_key = restored_from_legacy_key

        _ensure_modal_sdk()
        import modal as _modal

        cred_mounts = []
        try:
            from tools.credential_files import (
                get_credential_file_mounts,
                iter_skills_files,
                iter_cache_files,
            )

            for mount_entry in get_credential_file_mounts():
                cred_mounts.append(
                    _modal.Mount.from_local_file(
                        mount_entry["host_path"],
                        remote_path=mount_entry["container_path"],
                    )
                )
            for entry in iter_skills_files():
                cred_mounts.append(
                    _modal.Mount.from_local_file(
                        entry["host_path"],
                        remote_path=entry["container_path"],
                    )
                )
            cache_files = iter_cache_files()
            for entry in cache_files:
                cred_mounts.append(
                    _modal.Mount.from_local_file(
                        entry["host_path"],
                        remote_path=entry["container_path"],
                    )
                )
        except Exception as e:
            logger.debug("Modal: could not load credential file mounts: %s", e)

        forwarded_env = _collect_passthrough_env()
        if forwarded_env:
            logger.debug(
                "Modal: forwarding %d allowlisted env var(s) to sandbox: %s",
                len(forwarded_env), ", ".join(sorted(forwarded_env)),
            )

        # Persistent Modal Volumes (durable storage for scientific work).
        # Volumes live independently of the sandbox: they survive teardown and
        # are NOT captured by snapshot_filesystem() on cleanup — that's the
        # point. Because _provision_sandbox() mounts them on every create (base
        # image *and* snapshot restore, including recreate-on-death), they
        # re-attach on every session.
        volume_mounts: dict[str, Any] = {}
        for vol in self._modal_volumes:
            try:
                volume = _modal.Volume.from_name(
                    vol["name"], create_if_missing=vol.get("create_if_missing", True)
                )
                if vol.get("read_only") and hasattr(volume, "read_only"):
                    volume = volume.read_only()
                elif vol.get("read_only"):
                    logger.warning(
                        "Modal: read-only volumes unsupported by this SDK; "
                        "mounting %s read-write", vol["name"],
                    )
                volume_mounts[vol["mount_path"]] = volume
                logger.info(
                    "Modal: mounting Volume %r at %s", vol["name"], vol["mount_path"]
                )
            except Exception as e:
                logger.warning(
                    "Modal: could not prepare Volume %r at %s: %s",
                    vol.get("name"), vol.get("mount_path"), e,
                )

        self._cred_mounts = cred_mounts
        self._forwarded_env = forwarded_env
        self._volume_mounts = volume_mounts

        try:
            self._provision_sandbox()
        except Exception:
            self._worker.stop()
            raise

        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files("/root/.hermes"),
            upload_fn=self._modal_upload,
            delete_fn=self._modal_delete,
            bulk_upload_fn=self._modal_bulk_upload,
            bulk_download_fn=self._modal_bulk_download,
        )
        self._sync_manager.sync(force=True)
        self.init_session()

    # ------------------------------------------------------------------
    # Sandbox provisioning / recreate-on-death
    # ------------------------------------------------------------------

    def _provision_sandbox(self) -> None:
        """Create the Modal sandbox and set ``self._app`` / ``self._sandbox``.

        Reused by ``__init__`` and by ``_ensure_live_sandbox()`` so a sandbox
        Modal reaps mid-session can be rebuilt on demand. Reads the mounts,
        secrets, and volumes prepared once in ``__init__`` from ``self`` so a
        rebuild re-attaches the same credentials and Volumes.
        """
        _ensure_modal_sdk()
        import modal as _modal

        restored_snapshot_id = self._restored_snapshot_id if self._persistent else None
        restored_from_legacy_key = self._restored_from_legacy_key

        def _create_sandbox(image_spec: Any):
            app = _modal.App.lookup("hermes-agent", create_if_missing=True)
            create_kwargs = dict(self._sandbox_kwargs)
            if self._cred_mounts:
                existing_mounts = list(create_kwargs.pop("mounts", []))
                existing_mounts.extend(self._cred_mounts)
                create_kwargs["mounts"] = existing_mounts
            if self._forwarded_env:
                existing_secrets = list(create_kwargs.pop("secrets", []))
                existing_secrets.append(_modal.Secret.from_dict(self._forwarded_env))
                create_kwargs["secrets"] = existing_secrets
            if self._volume_mounts:
                merged_volumes = dict(create_kwargs.pop("volumes", {}) or {})
                merged_volumes.update(self._volume_mounts)
                create_kwargs["volumes"] = merged_volumes
            sandbox = _modal.Sandbox.create(
                "sleep", "infinity",
                image=image_spec,
                app=app,
                timeout=int(create_kwargs.pop("timeout", _DEFAULT_SANDBOX_TIMEOUT)),
                **create_kwargs,
            )
            return app, sandbox

        try:
            target_image_spec = restored_snapshot_id or self._image
            effective_image = _resolve_modal_image(target_image_spec)
            self._app, self._sandbox = self._worker.run(
                lambda: _create_sandbox(effective_image), timeout=300,
            )
        except Exception as exc:
            if not restored_snapshot_id:
                raise
            logger.warning(
                "Modal: failed to restore snapshot %s, retrying with base image: %s",
                restored_snapshot_id[:20], exc,
            )
            _delete_direct_snapshot(self._task_id, restored_snapshot_id)
            # Don't keep trying to restore a snapshot that just failed — the
            # base image is now the target for this and any future rebuild.
            self._restored_snapshot_id = None
            base_image = _resolve_modal_image(self._image)
            self._app, self._sandbox = self._worker.run(
                lambda: _create_sandbox(base_image), timeout=300,
            )
        else:
            if restored_snapshot_id and restored_from_legacy_key:
                _store_direct_snapshot(self._task_id, restored_snapshot_id)

        logger.info("Modal: sandbox created (task=%s)", self._task_id)

    def _ensure_live_sandbox(self, dead_generation: int) -> None:
        """Rebuild the sandbox after Modal reaped it, unless a peer already did.

        *dead_generation* is the generation the caller was using when it hit a
        dead-sandbox error; if the live generation has already moved past it,
        another thread rebuilt in the meantime and we do nothing.
        """
        with self._recreate_lock:
            if self._sandbox_generation != dead_generation:
                return  # A concurrent caller already rebuilt the sandbox.

            # Prefer the most recent snapshot so the rebuilt sandbox comes back
            # as close as possible to the pre-death filesystem. Anything written
            # since that snapshot is gone — that's inherent to losing the
            # sandbox; durable data belongs on a mounted Volume, which re-attaches.
            if self._persistent:
                restored_id, restored_legacy = _get_snapshot_restore_candidate(
                    self._task_id
                )
                if restored_id:
                    self._restored_snapshot_id = restored_id
                    self._restored_from_legacy_key = restored_legacy

            self._recreating = True
            try:
                self._provision_sandbox()
                self._sandbox_generation += 1
                if self._sync_manager is not None:
                    self._sync_manager.sync(force=True)
                self.init_session()
                logger.info(
                    "Modal: recreated sandbox on demand (task=%s, generation=%d)",
                    self._task_id, self._sandbox_generation,
                )
            finally:
                self._recreating = False

    def _run_on_worker(self, fn, *, timeout: int, op_label: str):
        """Run *fn* on the worker, rebuilding a dead sandbox once and retrying.

        Modal can terminate a sandbox out from under us (lifetime cap, eviction).
        Without this, every subsequent exec/file op — and any retry — hits the
        dead handle and fails identically, bricking the terminal for the rest of
        the session. Here we detect that, rebuild on demand, and retry once.
        """
        generation = self._sandbox_generation
        try:
            return self._worker.run(fn, timeout=timeout)
        except Exception as exc:
            # Don't attempt a rebuild from inside a rebuild's own resync, and
            # only rebuild for errors that actually mean "sandbox is gone".
            if self._recreating or not _is_sandbox_dead_error(exc):
                raise
            logger.warning(
                "Modal: sandbox for task %s died mid-%s (%s); recreating on demand",
                self._task_id, op_label, exc,
            )
            self._ensure_live_sandbox(dead_generation=generation)
            return self._worker.run(fn, timeout=timeout)

    def _modal_upload(self, host_path: str, remote_path: str) -> None:
        """Upload a single file via base64 piped through stdin."""
        content = Path(host_path).read_bytes()
        b64 = base64.b64encode(content).decode("ascii")
        container_dir = str(Path(remote_path).parent)
        cmd = (
            f"mkdir -p {shlex.quote(container_dir)} && "
            f"base64 -d > {shlex.quote(remote_path)}"
        )

        def _write():
            proc = self._sandbox.exec("bash", "-c", cmd)
            offset = 0
            chunk_size = self._STDIN_CHUNK_SIZE
            while offset < len(b64):
                proc.stdin.write(b64[offset:offset + chunk_size])
                proc.stdin.drain()
                offset += chunk_size
            proc.stdin.write_eof()
            proc.stdin.drain()
            proc.wait()

        self._run_on_worker(_write, timeout=30, op_label="upload")

    # Modal SDK stdin buffer limit (legacy server path).  The command-router
    # path allows 16 MB, but we must stay under the smaller 2 MB cap for
    # compatibility.  Chunks are written below this threshold and flushed
    # individually via drain().
    _STDIN_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB — safe for both transport paths

    def _modal_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        """Upload many files via tar archive piped through stdin.

        Builds a gzipped tar archive in memory and streams it into a
        ``base64 -d | tar xzf -`` pipeline via the process's stdin,
        avoiding the Modal SDK's 64 KB ``ARG_MAX_BYTES`` exec-arg limit.
        """
        if not files:
            return

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for host_path, remote_path in files:
                tar.add(host_path, arcname=remote_path.lstrip("/"))
        payload = base64.b64encode(buf.getvalue()).decode("ascii")

        parents = unique_parent_dirs(files)
        mkdir_part = quoted_mkdir_command(parents)
        cmd = f"{mkdir_part} && base64 -d | tar xzf - -C /"

        def _bulk():
            proc = self._sandbox.exec("bash", "-c", cmd)

            # Stream payload through stdin in chunks to stay under the
            # SDK's per-write buffer limit (2 MB legacy / 16 MB router).
            offset = 0
            chunk_size = self._STDIN_CHUNK_SIZE
            while offset < len(payload):
                proc.stdin.write(payload[offset:offset + chunk_size])
                proc.stdin.drain()
                offset += chunk_size

            proc.stdin.write_eof()
            proc.stdin.drain()

            exit_code = proc.wait()
            if exit_code != 0:
                stderr_text = proc.stderr.read()
                raise RuntimeError(
                    f"Modal bulk upload failed (exit {exit_code}): {stderr_text}"
                )

        self._run_on_worker(_bulk, timeout=120, op_label="bulk_upload")

    def _modal_bulk_download(self, dest: Path) -> None:
        """Download remote .hermes/ as a tar archive.

        Modal sandboxes always run as root, so /root/.hermes is hardcoded
        (consistent with iter_sync_files call on line 269).
        """
        def _download():
            proc = self._sandbox.exec(
                "bash", "-c", "tar cf - -C / root/.hermes"
            )
            data = proc.stdout.read()
            exit_code = proc.wait()
            if exit_code != 0:
                raise RuntimeError(f"Modal bulk download failed (exit {exit_code})")
            return data

        tar_bytes = self._run_on_worker(_download, timeout=120, op_label="bulk_download")
        if isinstance(tar_bytes, str):
            tar_bytes = tar_bytes.encode()
        dest.write_bytes(tar_bytes)

    def _modal_delete(self, remote_paths: list[str]) -> None:
        """Batch-delete remote files via exec."""
        rm_cmd = quoted_rm_command(remote_paths)

        def _rm():
            proc = self._sandbox.exec("bash", "-c", rm_cmd)
            proc.wait()

        self._run_on_worker(_rm, timeout=15, op_label="delete")

    def _before_execute(self) -> None:
        """Sync files to sandbox via FileSyncManager (rate-limited internally)."""
        self._sync_manager.sync()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None):
        """Return a _ThreadedProcessHandle wrapping a blocking Modal sandbox exec."""
        worker = self._worker

        def cancel():
            # Runs on a separate pool thread so it can interrupt an in-flight
            # exec rather than queueing behind it. Read self._sandbox live so a
            # recreate-on-death rebuild is picked up rather than a stale handle.
            sandbox = self._sandbox
            if sandbox is not None:
                worker.run(lambda: sandbox.terminate(), timeout=15)

        def exec_fn() -> tuple[str, int]:
            def _do():
                args = ["bash"]
                if login:
                    args.extend(["-l", "-c", cmd_string])
                else:
                    args.extend(["-c", cmd_string])
                # Read self._sandbox at call time so a retry after a rebuild
                # runs against the fresh sandbox, not the terminated one.
                process = self._sandbox.exec(*args, timeout=timeout)
                stdout = process.stdout.read()
                stderr = process.stderr.read()
                exit_code = process.wait()
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                output = stdout
                if stderr:
                    output = f"{stdout}\n{stderr}" if stdout else stderr
                return output, exit_code

            return self._run_on_worker(_do, timeout=timeout + 30, op_label="exec")

        return _ThreadedProcessHandle(exec_fn, cancel_fn=cancel)

    def cleanup(self):
        """Snapshot the filesystem (if persistent) then stop the sandbox."""
        if self._sandbox is None:
            return

        if self._sync_manager:
            logger.info("Modal: syncing files from sandbox...")
            self._sync_manager.sync_back()

        if self._persistent:
            try:
                def _snapshot():
                    img = self._sandbox.snapshot_filesystem()
                    return img.object_id

                try:
                    snapshot_id = self._worker.run(_snapshot, timeout=60)
                except Exception:
                    snapshot_id = None

                if snapshot_id:
                    _store_direct_snapshot(self._task_id, snapshot_id)
                    logger.info(
                        "Modal: saved filesystem snapshot %s for task %s",
                        snapshot_id[:20], self._task_id,
                    )
            except Exception as e:
                logger.warning("Modal: filesystem snapshot failed: %s", e)

        try:
            self._worker.run(lambda: self._sandbox.terminate(), timeout=15)
        except Exception:
            pass
        finally:
            self._worker.stop()
            self._sandbox = None
            self._app = None
