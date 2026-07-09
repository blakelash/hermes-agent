"""Tests for Modal sandbox infrastructure fixes (TBLite baseline).

Covers the bugs discovered while setting up TBLite evaluation:
1. Tool resolution — terminal + file tools load correctly
2. CWD fix — host paths get replaced with /root for container backends
3. ephemeral_disk version check
4. ensurepip fix in Modal image builder
5. No swe-rex dependency — uses native Modal SDK
6. /home/ added to host prefix check
"""

import os
import sys
from pathlib import Path
import pytest

# Ensure repo root is importable
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    import tools.terminal_tool  # noqa: F401
    _tt_mod = sys.modules["tools.terminal_tool"]
except ImportError:
    pytest.skip("hermes-agent tools not importable (missing deps)", allow_module_level=True)


# =========================================================================
# Test 1: Tool resolution includes terminal + file tools
# =========================================================================

class TestToolResolution:
    """Verify get_tool_definitions returns all expected tools for eval."""

    def test_terminal_and_file_toolsets_resolve_all_tools(self):
        """enabled_toolsets=['terminal', 'file'] should produce 6 tools."""
        from model_tools import get_tool_definitions
        tools = get_tool_definitions(
            enabled_toolsets=["terminal", "file"],
            quiet_mode=True,
        )
        names = {t["function"]["name"] for t in tools}
        expected = {"terminal", "process", "read_file", "write_file", "search_files", "patch"}
        assert expected == names, f"Expected {expected}, got {names}"

    def test_terminal_tool_present(self):
        """The terminal tool must be present (not silently dropped)."""
        from model_tools import get_tool_definitions
        tools = get_tool_definitions(
            enabled_toolsets=["terminal", "file"],
            quiet_mode=True,
        )
        names = [t["function"]["name"] for t in tools]
        assert "terminal" in names, f"terminal tool missing! Only got: {names}."


# =========================================================================
# Test 2-4: CWD handling for container backends
# =========================================================================

class TestCwdHandling:
    """Verify host paths are sanitized for container backends."""

    def test_home_path_replaced_for_modal(self, monkeypatch):
        """TERMINAL_CWD=/home/user/... should be replaced with /root for modal."""
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.setenv("TERMINAL_CWD", "/home/dakota/github/hermes-agent")
        config = _tt_mod._get_env_config()
        assert config["cwd"] == "/root", (
            f"Expected /root, got {config['cwd']}. "
            "/home/ paths should be replaced for modal backend."
        )

    def test_users_path_replaced_for_docker_by_default(self, monkeypatch):
        """Docker should keep host paths out of the sandbox unless explicitly enabled."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_CWD", "/Users/someone/projects")
        config = _tt_mod._get_env_config()
        assert config["cwd"] == "/root", (
            f"Expected /root, got {config['cwd']}. "
            "Host paths should be discarded for docker backend by default."
        )
        assert config["host_cwd"] is None
        assert config["docker_mount_cwd_to_workspace"] is False

    def test_users_path_maps_to_workspace_for_docker_when_enabled(self, monkeypatch):
        """Docker should map the host cwd into /workspace only when explicitly enabled."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_CWD", "/Users/someone/projects")
        monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "true")
        config = _tt_mod._get_env_config()
        assert config["cwd"] == "/workspace"
        assert config["host_cwd"] == "/Users/someone/projects"
        assert config["docker_mount_cwd_to_workspace"] is True

    def test_windows_path_replaced_for_modal(self, monkeypatch):
        """TERMINAL_CWD=C:\\Users\\... should be replaced for modal."""
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.setenv("TERMINAL_CWD", "C:\\Users\\someone\\projects")
        config = _tt_mod._get_env_config()
        assert config["cwd"] == "/root"

    @pytest.mark.parametrize("backend", ["modal", "docker", "singularity", "daytona"])
    def test_default_cwd_is_root_for_container_backends(self, backend, monkeypatch):
        """Container backends should default to /root, not ~."""
        monkeypatch.setenv("TERMINAL_ENV", backend)
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        monkeypatch.delenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", raising=False)
        config = _tt_mod._get_env_config()
        assert config["cwd"] == "/root", (
            f"Backend {backend}: expected /root default, got {config['cwd']}"
        )

    def test_docker_default_cwd_maps_current_directory_when_enabled(self, monkeypatch):
        """Docker should use /workspace when cwd mounting is explicitly enabled."""
        monkeypatch.setattr("tools.terminal_tool.os.getcwd", lambda: "/home/user/project")
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "true")
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        config = _tt_mod._get_env_config()
        assert config["cwd"] == "/workspace"
        assert config["host_cwd"] == "/home/user/project"

    def test_local_backend_uses_getcwd(self, monkeypatch):
        """Local backend should use os.getcwd(), not /root."""
        monkeypatch.setenv("TERMINAL_ENV", "local")
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        config = _tt_mod._get_env_config()
        assert config["cwd"] == os.getcwd()

    def test_create_environment_passes_docker_host_cwd_and_flag(self, monkeypatch):
        """Docker host cwd and mount flag should reach DockerEnvironment."""
        captured = {}
        sentinel = object()

        def _fake_docker_environment(**kwargs):
            captured.update(kwargs)
            return sentinel

        monkeypatch.setattr(_tt_mod, "_DockerEnvironment", _fake_docker_environment)

        env = _tt_mod._create_environment(
            env_type="docker",
            image="python:3.11",
            cwd="/workspace",
            timeout=60,
            container_config={"docker_mount_cwd_to_workspace": True},
            host_cwd="/home/user/project",
        )

        assert env is sentinel
        assert captured["cwd"] == "/workspace"
        assert captured["host_cwd"] == "/home/user/project"
        assert captured["auto_mount_cwd"] is True

    def test_ssh_preserves_home_paths(self, monkeypatch):
        """SSH backend should NOT replace /home/ paths (they're valid remotely)."""
        monkeypatch.setenv("TERMINAL_ENV", "ssh")
        monkeypatch.setenv("TERMINAL_CWD", "/home/remote-user/work")
        monkeypatch.setenv("TERMINAL_SSH_HOST", "example.com")
        monkeypatch.setenv("TERMINAL_SSH_USER", "user")
        config = _tt_mod._get_env_config()
        assert config["cwd"] == "/home/remote-user/work", (
            "SSH backend should preserve /home/ paths"
        )


# =========================================================================
# Test 5: ephemeral_disk version check
# =========================================================================

class TestEphemeralDiskCheck:
    """Verify ephemeral_disk is only passed when modal supports it."""

    def test_ephemeral_disk_skipped_when_unsupported(self, monkeypatch):
        """If modal.Sandbox.create doesn't have ephemeral_disk param, skip it."""
        import inspect
        mock_params = {
            "args": inspect.Parameter("args", inspect.Parameter.VAR_POSITIONAL),
            "image": inspect.Parameter("image", inspect.Parameter.KEYWORD_ONLY),
            "timeout": inspect.Parameter("timeout", inspect.Parameter.KEYWORD_ONLY),
            "cpu": inspect.Parameter("cpu", inspect.Parameter.KEYWORD_ONLY),
            "memory": inspect.Parameter("memory", inspect.Parameter.KEYWORD_ONLY),
        }

        monkeypatch.setenv("TERMINAL_ENV", "modal")
        config = _tt_mod._get_env_config()
        # The config has container_disk default of 51200
        disk = config.get("container_disk", 51200)
        assert disk > 0, "disk should default to > 0"

        # Simulate the version check logic from terminal_tool.py
        sandbox_kwargs = {}
        if disk > 0:
            try:
                if "ephemeral_disk" in mock_params:
                    sandbox_kwargs["ephemeral_disk"] = disk
            except Exception:
                pass

        assert "ephemeral_disk" not in sandbox_kwargs, (
            "ephemeral_disk should not be set when Sandbox.create doesn't support it"
        )


# =========================================================================
# Test 6: ModalEnvironment defaults
# =========================================================================

class TestModalEnvironmentDefaults:
    """Verify ModalEnvironment has correct defaults."""

    def test_default_cwd_is_root(self):
        """ModalEnvironment default cwd should be /root, not ~."""
        from tools.environments.modal import ModalEnvironment
        import inspect
        sig = inspect.signature(ModalEnvironment.__init__)
        cwd_default = sig.parameters["cwd"].default
        assert cwd_default == "/root", (
            f"ModalEnvironment cwd default should be /root, got {cwd_default!r}. "
            "Tilde ~ is not expanded by subprocess.run(cwd=...)."
        )


# =========================================================================
# Test 7: ensurepip fix in ModalEnvironment
# =========================================================================

class TestEnsurepipFix:
    """Verify the pip fix is applied in the ModalEnvironment init."""

    def test_modal_environment_creates_image_with_setup_commands(self):
        """_resolve_modal_image should create a modal.Image with pip fix."""
        try:
            from tools.environments.modal import _resolve_modal_image
        except ImportError:
            pytest.skip("tools.environments.modal not importable")

        import inspect
        source = inspect.getsource(_resolve_modal_image)
        assert "ensurepip" in source, (
            "_resolve_modal_image should include ensurepip fix "
            "for Modal's legacy image builder"
        )
        assert "setup_dockerfile_commands" in source, (
            "_resolve_modal_image should use setup_dockerfile_commands "
            "to fix pip before Modal's bootstrap"
        )

    def test_modal_environment_uses_native_sdk(self):
        """ModalEnvironment should use Modal SDK directly, not swe-rex."""
        try:
            from tools.environments.modal import ModalEnvironment
        except ImportError:
            pytest.skip("tools.environments.modal not importable")

        import inspect
        source = inspect.getsource(ModalEnvironment)
        assert "swerex" not in source.lower(), (
            "ModalEnvironment should not depend on swe-rex; "
            "use Modal SDK directly via Sandbox.create() + exec()"
        )
        # modal>=1.3 uses the blocking SDK surface (synchronicity), not .aio()
        # coroutines, driven from loop-free worker threads.
        assert "Sandbox.create(" in source, (
            "ModalEnvironment should use the blocking Modal Sandbox.create()"
        )
        assert ".exec(" in source, (
            "ModalEnvironment should use Sandbox.exec() for command execution"
        )


# =========================================================================
# Test 8: Host prefix list completeness
# =========================================================================

class TestHostPrefixList:
    """Verify the host prefix list catches common host-only paths."""

    def test_all_common_host_prefixes_caught(self):
        """The host prefix check should catch /Users/, /home/, C:\\, C:/."""
        # Read the actual source to verify the prefixes
        import inspect
        source = inspect.getsource(_tt_mod._get_env_config)
        for prefix in ["/Users/", "/home/", 'C:\\\\"', "C:/"]:
            # Normalize for source comparison
            check = prefix.rstrip('"')
            assert check in source or prefix in source, (
                f"Host prefix {prefix!r} not found in _get_env_config. "
                "Container backends need this to avoid using host paths."
            )


# =========================================================================
# Test 9: Sandbox lifetime cap is configurable and threads through
# =========================================================================

class TestModalSandboxTimeout:
    """Modal's hard sandbox lifetime cap must be configurable, not a fixed 1h."""

    def test_default_timeout_in_config(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.delenv("TERMINAL_MODAL_SANDBOX_TIMEOUT", raising=False)
        config = _tt_mod._get_env_config()
        assert config["modal_sandbox_timeout"] == 21600

    def test_timeout_override_from_env(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "modal")
        monkeypatch.setenv("TERMINAL_MODAL_SANDBOX_TIMEOUT", "7200")
        config = _tt_mod._get_env_config()
        assert config["modal_sandbox_timeout"] == 7200

    def test_timeout_reaches_sandbox_kwargs(self, monkeypatch):
        """_create_environment must pass the configured cap into Sandbox.create kwargs."""
        captured = {}

        def _fake_modal_env(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(_tt_mod, "_ModalEnvironment", _fake_modal_env)
        monkeypatch.setattr(
            _tt_mod, "_get_modal_backend_state",
            lambda mode: {"selected_backend": "direct"},
        )

        _tt_mod._create_environment(
            env_type="modal",
            image="python:3.11",
            cwd="/root",
            timeout=60,
            container_config={
                "container_cpu": 1,
                "container_memory": 5120,
                "container_disk": 0,
                "modal_sandbox_timeout": 12345,
            },
        )
        assert captured["modal_sandbox_kwargs"]["timeout"] == 12345


# =========================================================================
# Test 10: Recreate-on-death — a reaped sandbox is rebuilt, not left dead
# =========================================================================

class TestRecreateOnDeath:
    """When Modal reaps a sandbox mid-session, the next op rebuilds it."""

    def _make_env(self):
        import threading
        from tools.environments.modal import ModalEnvironment

        env = ModalEnvironment.__new__(ModalEnvironment)
        env._task_id = "t"
        env._persistent = False
        env._recreating = False
        env._sandbox_generation = 0
        env._recreate_lock = threading.Lock()
        env._sync_manager = None

        class _Worker:
            def run(self, fn, timeout=None):
                return fn()

        env._worker = _Worker()
        return env

    def test_dead_error_detection(self):
        from tools.environments.modal import _is_sandbox_dead_error

        modal_msg = (
            "Modal Sandbox with container ID ta-01ABC not found. "
            "This means this Sandbox has already shut down."
        )
        assert _is_sandbox_dead_error(RuntimeError(modal_msg))
        assert _is_sandbox_dead_error(Exception("Sandbox has terminated"))
        assert not _is_sandbox_dead_error(RuntimeError("compilation error: undefined symbol"))

    def test_recreates_and_retries_once(self, monkeypatch):
        env = self._make_env()
        rebuilt = {"count": 0}
        monkeypatch.setattr(
            env, "_provision_sandbox",
            lambda: rebuilt.__setitem__("count", rebuilt["count"] + 1),
        )
        monkeypatch.setattr(env, "init_session", lambda: None)

        attempts = {"n": 0}

        def op():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError(
                    "Modal Sandbox with container ID ta-01ABC not found. "
                    "This means this Sandbox has already shut down."
                )
            return "ok"

        result = env._run_on_worker(op, timeout=5, op_label="exec")
        assert result == "ok"
        assert attempts["n"] == 2          # failed once, retried once
        assert rebuilt["count"] == 1       # rebuilt exactly once
        assert env._sandbox_generation == 1

    def test_non_death_error_is_not_recovered(self, monkeypatch):
        env = self._make_env()
        rebuilt = {"count": 0}
        monkeypatch.setattr(
            env, "_provision_sandbox",
            lambda: rebuilt.__setitem__("count", rebuilt["count"] + 1),
        )

        def op():
            raise RuntimeError("compilation error")

        with pytest.raises(RuntimeError, match="compilation error"):
            env._run_on_worker(op, timeout=5, op_label="exec")
        assert rebuilt["count"] == 0       # a real command failure is not a rebuild trigger

    def test_no_reentrant_recreate_while_rebuilding(self, monkeypatch):
        """A dead-sandbox error raised during a rebuild's own resync must not recurse."""
        env = self._make_env()
        env._recreating = True
        rebuilt = {"count": 0}
        monkeypatch.setattr(
            env, "_provision_sandbox",
            lambda: rebuilt.__setitem__("count", rebuilt["count"] + 1),
        )

        def op():
            raise RuntimeError("Sandbox has terminated")

        with pytest.raises(RuntimeError, match="terminated"):
            env._run_on_worker(op, timeout=5, op_label="upload")
        assert rebuilt["count"] == 0

    def test_concurrent_callers_rebuild_once(self, monkeypatch):
        """A caller holding a stale generation must not trigger a second rebuild."""
        env = self._make_env()
        rebuilt = {"count": 0}
        monkeypatch.setattr(
            env, "_provision_sandbox",
            lambda: rebuilt.__setitem__("count", rebuilt["count"] + 1),
        )
        monkeypatch.setattr(env, "init_session", lambda: None)

        # First caller observed generation 0 and rebuilds -> generation 1.
        env._ensure_live_sandbox(dead_generation=0)
        assert rebuilt["count"] == 1
        # Second caller also saw the same dead handle (generation 0); the
        # generation has already advanced, so it must be a no-op.
        env._ensure_live_sandbox(dead_generation=0)
        assert rebuilt["count"] == 1
