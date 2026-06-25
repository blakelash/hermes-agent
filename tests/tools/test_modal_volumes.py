"""Tests for Modal Volume support (persistent storage for scientific work).

Covers:
1. Config normalization across all accepted input shapes.
2. Env-var parsing into the terminal config dict.
3. _create_environment forwarding volumes to the (managed) Modal backend.
4. Both Modal backends accepting the modal_volumes kwarg.
5. The system-prompt hint that makes the agent aware of persistent paths.
"""

import sys
from pathlib import Path

import pytest

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    import tools.terminal_tool  # noqa: F401
    _tt_mod = sys.modules["tools.terminal_tool"]
    from tools.environments.modal_volumes import (
        normalize_modal_volumes,
        parse_modal_volumes_env,
        describe_modal_volumes,
    )
except ImportError:
    pytest.skip("hermes-agent tools not importable (missing deps)", allow_module_level=True)


# =========================================================================
# Test 1: normalize_modal_volumes — input shapes
# =========================================================================

class TestNormalizeModalVolumes:
    def test_empty_inputs(self):
        assert normalize_modal_volumes(None) == []
        assert normalize_modal_volumes("") == []
        assert normalize_modal_volumes([]) == []
        assert normalize_modal_volumes({}) == []

    def test_list_of_dicts_full(self):
        out = normalize_modal_volumes(
            [{"name": "sci", "mount_path": "/work", "create_if_missing": False, "read_only": True}]
        )
        assert out == [
            {"name": "sci", "mount_path": "/work", "create_if_missing": False, "read_only": True}
        ]

    def test_defaults_applied(self):
        out = normalize_modal_volumes([{"name": "sci", "mount_path": "/work"}])
        assert out[0]["create_if_missing"] is True
        assert out[0]["read_only"] is False

    def test_path_alias(self):
        out = normalize_modal_volumes([{"name": "sci", "path": "/work"}])
        assert out[0]["mount_path"] == "/work"

    def test_dict_shorthand(self):
        out = normalize_modal_volumes({"/work": "sci", "/data": "datasets"})
        by_name = {v["name"]: v["mount_path"] for v in out}
        assert by_name == {"sci": "/work", "datasets": "/data"}

    def test_string_shorthand(self):
        out = normalize_modal_volumes(["sci:/work"])
        assert out == [
            {"name": "sci", "mount_path": "/work", "create_if_missing": True, "read_only": False}
        ]

    def test_json_string(self):
        out = normalize_modal_volumes('[{"name": "sci", "mount_path": "/work"}]')
        assert out[0]["name"] == "sci"
        assert out[0]["mount_path"] == "/work"

    def test_bare_string_shorthand_without_json(self):
        out = normalize_modal_volumes("sci:/work")
        assert out == [
            {"name": "sci", "mount_path": "/work", "create_if_missing": True, "read_only": False}
        ]

    def test_invalid_entries_skipped(self):
        # missing name, missing path, relative path, wrong type, no colon
        out = normalize_modal_volumes(
            [
                {"mount_path": "/work"},          # no name
                {"name": "sci"},                  # no path
                {"name": "x", "mount_path": "rel"},  # not absolute
                123,                               # wrong type
                "noseparator",                    # no colon
                {"name": "ok", "mount_path": "/keep"},
            ]
        )
        assert out == [
            {"name": "ok", "mount_path": "/keep", "create_if_missing": True, "read_only": False}
        ]

    def test_trailing_slash_stripped(self):
        out = normalize_modal_volumes([{"name": "sci", "mount_path": "/work/"}])
        assert out[0]["mount_path"] == "/work"

    def test_dedup_last_wins_on_mount_path(self):
        out = normalize_modal_volumes(
            [
                {"name": "a", "mount_path": "/work"},
                {"name": "b", "mount_path": "/work"},
            ]
        )
        assert len(out) == 1
        assert out[0]["name"] == "b"

    def test_parse_env_alias(self):
        assert parse_modal_volumes_env('[{"name":"s","mount_path":"/w"}]')[0]["name"] == "s"

    def test_describe(self):
        desc = describe_modal_volumes(
            [
                {"name": "sci", "mount_path": "/work", "read_only": False},
                {"name": "ref", "mount_path": "/ref", "read_only": True},
            ]
        )
        assert "/work -> Modal Volume 'sci'" in desc
        assert "/ref -> Modal Volume 'ref' (read-only)" in desc
        assert describe_modal_volumes([]) == ""


# =========================================================================
# Test 2: _get_env_config parses TERMINAL_MODAL_VOLUMES
# =========================================================================

class TestEnvConfigParsing:
    def test_modal_volumes_parsed_for_modal_backend(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.setenv(
            "TERMINAL_MODAL_VOLUMES", '[{"name": "sci", "mount_path": "/work"}]'
        )
        config = _tt_mod._get_env_config()
        assert config["modal_volumes"] == [
            {"name": "sci", "mount_path": "/work", "create_if_missing": True, "read_only": False}
        ]

    def test_modal_volumes_empty_for_local_backend(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "local")
        monkeypatch.setenv(
            "TERMINAL_MODAL_VOLUMES", '[{"name": "sci", "mount_path": "/work"}]'
        )
        config = _tt_mod._get_env_config()
        # Container-only config is not parsed for local backend.
        assert config["modal_volumes"] == []

    def test_modal_volumes_default_empty(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.delenv("TERMINAL_MODAL_VOLUMES", raising=False)
        config = _tt_mod._get_env_config()
        assert config["modal_volumes"] == []


# =========================================================================
# Test 3: _create_environment forwards volumes to the Modal backend
# =========================================================================

class TestCreateEnvironmentForwarding:
    def test_modal_volumes_reach_managed_modal_backend(self, monkeypatch):
        """modal_volumes in container_config should reach the Modal env ctor.

        We force the managed backend (no direct Modal creds needed) and stub it
        with a fake that captures kwargs.
        """
        captured = {}
        sentinel = object()

        def _fake_env(**kwargs):
            captured.update(kwargs)
            return sentinel

        monkeypatch.setattr(_tt_mod, "_ManagedModalEnvironment", _fake_env)
        monkeypatch.setattr(
            _tt_mod,
            "_get_modal_backend_state",
            lambda *_a, **_k: {"selected_backend": "managed"},
        )

        vols = [{"name": "sci", "mount_path": "/work", "create_if_missing": True, "read_only": False}]
        env = _tt_mod._create_environment(
            env_type="modal",
            image="python:3.11",
            cwd="/root",
            timeout=60,
            container_config={"modal_volumes": vols},
            task_id="t1",
        )
        assert env is sentinel
        assert captured["modal_volumes"] == vols


# =========================================================================
# Test 4: both backends accept the modal_volumes kwarg
# =========================================================================

class TestBackendSignatures:
    def test_direct_modal_accepts_modal_volumes(self):
        import inspect
        from tools.environments.modal import ModalEnvironment
        assert "modal_volumes" in inspect.signature(ModalEnvironment.__init__).parameters

    def test_managed_modal_accepts_modal_volumes(self):
        import inspect
        from tools.environments.managed_modal import ManagedModalEnvironment
        assert "modal_volumes" in inspect.signature(ManagedModalEnvironment.__init__).parameters


# =========================================================================
# Test 5: agent awareness — system-prompt hint
# =========================================================================

class TestPromptHint:
    def test_volume_hint_present_for_modal(self, monkeypatch):
        import agent.prompt_builder as pb
        # Avoid any network probe of the backend.
        monkeypatch.setattr(pb, "_probe_remote_backend", lambda *_a, **_k: None)
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.setenv(
            "TERMINAL_MODAL_VOLUMES", '[{"name": "sci", "mount_path": "/work"}]'
        )
        hints = pb.build_environment_hints()
        assert "Persistent storage" in hints
        assert "/work -> Modal Volume 'sci'" in hints
        assert "EPHEMERAL" in hints

    def test_no_volume_hint_when_unset(self, monkeypatch):
        import agent.prompt_builder as pb
        monkeypatch.setattr(pb, "_probe_remote_backend", lambda *_a, **_k: None)
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.delenv("TERMINAL_MODAL_VOLUMES", raising=False)
        hints = pb.build_environment_hints()
        assert "Persistent storage" not in hints
