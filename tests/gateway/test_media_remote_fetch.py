"""Tests for MEDIA egress (gateway/media_egress.py).

Artifacts produced inside a remote sandbox must be fetched to the host before
media delivery (which only reads host paths); local backends, host-visible
paths, oversized files, and transport failures must all pass through
untouched so the existing not-found warning names the real sandbox path.
"""

from __future__ import annotations

import base64

import pytest

import gateway.media_egress as egress
from hermes_constants import get_hermes_home

PNG_BYTES = b"\x89PNG\r\n\x1a\n fake image bytes"
SANDBOX_PATH = "/work/rna/sess1/fig.png"


class _FakeRemoteEnv:
    """Answers wc -c / base64 like a sandbox holding PNG_BYTES at SANDBOX_PATH."""

    def __init__(self, files=None):
        self.files = files if files is not None else {SANDBOX_PATH: PNG_BYTES}
        self.commands = []

    def execute(self, command, cwd=None, timeout=None):
        self.commands.append(command)
        for path, data in self.files.items():
            quoted = f"'{path}'" if "'" not in path else path
            if command in (f"wc -c < {quoted}", f"wc -c < {path}"):
                return {"output": str(len(data)), "returncode": 0}
            if command in (f"base64 < {quoted}", f"base64 < {path}"):
                return {"output": base64.encodebytes(data).decode(), "returncode": 0}
        return {"output": "", "returncode": 1}


@pytest.fixture()
def remote_backend(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "modal")


@pytest.fixture()
def fake_env(monkeypatch):
    env = _FakeRemoteEnv()
    monkeypatch.setattr(egress, "_session_env", lambda task_id: env)
    return env


def test_fetch_writes_host_copy(remote_backend, fake_env):
    local = egress.fetch_remote_file(SANDBOX_PATH, "sess1")
    assert local is not None
    p = get_hermes_home() / "cache" / "media_egress"
    assert str(p) in local and local.endswith("-fig.png")
    with open(local, "rb") as f:
        assert f.read() == PNG_BYTES


def test_rewrite_points_tag_at_host_copy(remote_backend, fake_env):
    text = f"Here is the figure!\nMEDIA:{SANDBOX_PATH}"
    out = egress.rewrite_remote_media_tags(text, "sess1")
    assert f"MEDIA:{SANDBOX_PATH}" not in out
    assert "MEDIA:" in out and "-fig.png" in out
    assert "Here is the figure!" in out


def test_local_backend_never_touches_text(monkeypatch, fake_env):
    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    text = f"MEDIA:{SANDBOX_PATH}"
    assert egress.rewrite_remote_media_tags(text, "sess1") == text
    assert fake_env.commands == []


def test_host_visible_path_passes_through(remote_backend, fake_env, tmp_path):
    real = tmp_path / "already-here.png"
    real.write_bytes(PNG_BYTES)
    text = f"MEDIA:{real}"
    assert egress.rewrite_remote_media_tags(text, "sess1") == text
    assert fake_env.commands == []


def test_missing_in_sandbox_leaves_tag(remote_backend, monkeypatch):
    env = _FakeRemoteEnv(files={})
    monkeypatch.setattr(egress, "_session_env", lambda task_id: env)
    text = f"MEDIA:{SANDBOX_PATH}"
    assert egress.rewrite_remote_media_tags(text, "sess1") == text


def test_over_cap_leaves_tag(remote_backend, fake_env, monkeypatch):
    monkeypatch.setattr(egress, "_max_fetch_bytes", lambda: 4)
    text = f"MEDIA:{SANDBOX_PATH}"
    assert egress.rewrite_remote_media_tags(text, "sess1") == text
    # Size was probed but the payload never streamed.
    assert any(c.startswith("wc -c") for c in fake_env.commands)
    assert not any(c.startswith("base64") for c in fake_env.commands)


def test_transport_error_is_soft(remote_backend, monkeypatch):
    def _boom(task_id):
        raise RuntimeError("sandbox gone")

    monkeypatch.setattr(egress, "_session_env", _boom)
    text = f"MEDIA:{SANDBOX_PATH}"
    assert egress.rewrite_remote_media_tags(text, "sess1") == text


def test_truncated_transfer_is_dropped(remote_backend, monkeypatch):
    env = _FakeRemoteEnv()

    real_execute = env.execute

    def _lying_execute(command, cwd=None, timeout=None):
        res = real_execute(command, cwd=cwd, timeout=timeout)
        if command.startswith("wc -c"):
            res = {"output": str(len(PNG_BYTES) + 100), "returncode": 0}
        return res

    env.execute = _lying_execute
    monkeypatch.setattr(egress, "_session_env", lambda task_id: env)
    assert egress.fetch_remote_file(SANDBOX_PATH, "sess1") is None


def test_multiple_tags_rewritten_independently(remote_backend, monkeypatch, tmp_path):
    other = "/work/rna/sess1/data.csv"
    env = _FakeRemoteEnv(files={SANDBOX_PATH: PNG_BYTES, other: b"a,b\n1,2\n"})
    monkeypatch.setattr(egress, "_session_env", lambda task_id: env)
    text = f"MEDIA:{SANDBOX_PATH}\nMEDIA:{other}"
    out = egress.rewrite_remote_media_tags(text, "sess1")
    assert SANDBOX_PATH not in out and other not in out
    assert out.count("MEDIA:") == 2


def test_gc_removes_stale_copies(remote_backend, fake_env, monkeypatch):
    import os
    import time as _time

    d = get_hermes_home() / "cache" / "media_egress"
    d.mkdir(parents=True, exist_ok=True)
    stale = d / "old-file.png"
    stale.write_bytes(b"x")
    old = _time.time() - (egress._EGRESS_GC_SECONDS + 60)
    os.utime(stale, (old, old))

    egress.fetch_remote_file(SANDBOX_PATH, "sess1")
    assert not stale.exists()
