# Goal: Modal Volumes in Sandboxes (permanent storage for scientific work)

Branch: `feat/modal-volumes-sandbox`

## Objective
Mount a Modal Volume into Hermes Modal sandboxes so scientific artifacts (large
data, checkpoints, figures) persist past sandbox teardown, are shareable across
concurrent kanban tasks / profiles / agents, and are browsable (CLI, Modal
dashboard, and our own future dashboard via the Modal SDK).

## Why not the existing snapshot / why not GCS
- The per-`task_id` filesystem snapshot (`tools/environments/modal.py:442`,
  `cleanup()` → `snapshot_filesystem()` → `modal_snapshots.json`) persists files
  but is opaque, task-scoped, all-or-nothing, no provenance. Good for *env
  continuity*, unusable as a *results store*. Keep it for resume only.
- A Modal Volume IS browsable (`modal volume ls/get`, Modal dashboard, and the
  Python SDK `Volume.from_name().listdir()/read_file()` our dashboard can call),
  so GCS is not needed for browsing. GCS deferred; revisit only for
  outside-Modal reach or many-small-file datasets → then use CloudBucketMount
  (`optional-skills/mlops/modal/references/advanced-usage.md:209`), not a
  separate publish tier.

## Current state (verified)
- No Volume support exists: `Volume` absent from `modal.py` / `managed_modal.py`.
- Sandbox FS is remote/separate from agent host; only `/root/.hermes` is synced
  (`tools/environments/file_sync.py`). Sandbox created at `modal.py:248`.
- Two backends to consider: direct `ModalEnvironment` (`tools/environments/modal.py`)
  and gateway-owned `ManagedModalEnvironment` (`tools/environments/managed_modal.py`).

## Plan (high level — refine before coding)
1. Config: which volume name(s), mount path (default `/work`), create-if-missing.
2. Direct backend: build `modal.Volume.from_name(...)` and pass via
   `volumes={mount_path: vol}` into `Sandbox.create()` (`modal.py:248`).
3. Managed backend: thread the same through the gateway sandbox-create payload
   (`managed_modal.py:190` area).
4. Write discipline: namespace per run (`/work/<run_id>/...`); ensure
   `commit()`/`reload()` semantics are handled (Volumes not strongly consistent
   under concurrent writers).
5. Interplay with snapshot: confirm volume-mounted paths are excluded from / not
   broken by `snapshot_filesystem()` on cleanup.
6. Discovery/provenance (store-agnostic): per-run `manifest.json` + Hindsight
   pointer memory so other agents/profiles can find results.
7. Tests: extend `tests/tools/test_managed_modal_environment.py` /
   `test_modal_sandbox_fixes.py` for volume mount + persistence-across-teardown.

## Constraints / footguns
- Multi-writer drift across kanban tasks → per-run namespacing + commit/reload.
- Sandbox-mirror write bug (`agent/file_safety.py` #32049) → fixed `/work` mount
  avoids host-vs-mirror path ambiguity.
- Don't regress the snapshot resume path.

## Status
IMPLEMENTED + VERIFIED (2026-06-25).

Changes:
- `tools/environments/modal_volumes.py` (new): dependency-free config
  normalizer/describer (`normalize_modal_volumes`, `parse_modal_volumes_env`,
  `describe_modal_volumes`).
- `tools/environments/modal.py`: `ModalEnvironment` takes `modal_volumes`,
  builds `modal.Volume.from_name(...)` and mounts via `volumes={path: vol}` on
  every create (base image + snapshot restore). Read-only honored when SDK
  supports it.
- `tools/environments/managed_modal.py`: `ManagedModalEnvironment` takes
  `modal_volumes`, sends a `volumes` array in the gateway create payload only
  when configured (gateway-dependent; direct mode is the tested path).
- `tools/terminal_tool.py`: parse `TERMINAL_MODAL_VOLUMES`, thread through
  `_get_env_config` -> container_config -> `_create_environment` -> both envs.
- `hermes_cli/config.py`: `terminal.modal_volumes` default + env bridge to
  `TERMINAL_MODAL_VOLUMES` (JSON-encoded).
- `agent/prompt_builder.py`: system-prompt hint telling the agent which paths
  are persistent vs ephemeral and to write durable artifacts under
  `<mount>/<run-id>/`.
- `tests/tools/test_modal_volumes.py` (new): 28 tests (normalization, env
  parsing, forwarding, signatures, prompt hint).

Verification:
- pytest test_modal_volumes.py + test_modal_sandbox_fixes.py -> 40 passed.
- LIVE: wrote sentinel to /work in sandbox A, tore it down, mounted the SAME
  volume in a separate sandbox B, read it back -> PASS. Throwaway volume deleted.

Config example (config.yaml):
  terminal:
    env: modal
    modal_volumes:
      - {name: hermes-science, mount_path: /work, create_if_missing: true}

Follow-ups (not blocking):
- Pre-existing `sync_back` utf-8 decode error on `.hermes` tar download
  (`_modal_bulk_download`) — unrelated to volumes, seen during live test.
- Managed-gateway `volumes` payload is speculative until the gateway supports it.
- Discovery/provenance layer (manifest.json + Hindsight pointer) still TODO.
