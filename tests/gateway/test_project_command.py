"""Tests for the /project gateway slash command.

/project binds a chat to a project: ``set``/``new`` pin the chat's sticky
default and either bind the live (still-empty) session in place or start a
fresh bound session when the conversation already has activity — the frozen
system prompt is never mutated mid-conversation. ``clear`` is the symmetric
unbind; ``list``/bare show state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import tools.projects as projects
import tools.terminal_tool as tt
from gateway.platforms.base import MessageEvent
from gateway.session import Platform, SessionSource
from hermes_state import SessionDB

SESSION_ID = "sess_123"
SESSION_KEY = "telegram:12345:67890"


def _make_event(text="/project"):
    source = SessionSource(
        platform=Platform.TELEGRAM, user_id="12345", chat_id="67890", user_name="u"
    )
    return MessageEvent(text=text, source=source)


class _FakeEntry:
    def __init__(self, session_id=SESSION_ID, session_key=SESSION_KEY):
        self.session_id = session_id
        self.session_key = session_key
        self.project_slug = ""


class _FakeStore:
    """Minimal session-store double tracking bind/reset interplay."""

    def __init__(self, db):
        self._db = db
        self.entry = _FakeEntry()
        self.reset_count = 0

    def get_or_create_session(self, source, force_new=False):
        return self.entry

    def set_session_project(self, session_key, slug):
        assert session_key == self.entry.session_key
        self.entry.project_slug = (slug or "").strip()
        return self.entry

    def simulate_reset(self):
        """What reset_session does: new id, sticky default re-adopted."""
        self.reset_count += 1
        new = _FakeEntry(session_id=f"sess_after_reset_{self.reset_count}")
        new.project_slug = self._db.get_chat_project_default(SESSION_KEY) or ""
        self.entry = new
        return new


def _make_runner(tmp_path):
    from gateway.run import GatewayRunner

    db = SessionDB(db_path=tmp_path / "state.db")
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._session_db = db
    runner.session_store = _FakeStore(db)
    runner._evict_cached_agent = MagicMock()
    runner._handle_reset_command = AsyncMock(
        side_effect=lambda event: runner.session_store.simulate_reset()
    )
    return runner, db


@pytest.fixture()
def runner_db(tmp_path):
    runner, db = _make_runner(tmp_path)
    yield runner, db
    tt.clear_task_env_overrides(SESSION_ID)
    db.close()


# ──────────────────────────────────────────────────────────────────────────
# status / list
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bare_project_shows_unbound(runner_db):
    runner, _ = runner_db
    out = await runner._handle_project_command(_make_event("/project"))
    assert "No project bound" in out and "/project set" in out


@pytest.mark.asyncio
async def test_list_marks_current(runner_db):
    runner, db = runner_db
    projects.create_project("RNA")
    projects.create_project("Proteomics")
    db.create_session(SESSION_ID, "telegram")
    await runner._handle_project_command(_make_event("/project set rna"))
    out = await runner._handle_project_command(_make_event("/project list"))
    assert "RNA (rna)" in out and "this chat" in out
    assert "Proteomics (proteomics)" in out


@pytest.mark.asyncio
async def test_list_empty_registry(runner_db):
    runner, _ = runner_db
    out = await runner._handle_project_command(_make_event("/project list"))
    assert "/project new" in out


# ──────────────────────────────────────────────────────────────────────────
# set — resolution and errors
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_unknown_name_suggests_new(runner_db):
    runner, db = runner_db
    out = await runner._handle_project_command(_make_event("/project set nope"))
    assert "No project matches" in out and "/project new nope" in out
    assert runner.session_store.entry.project_slug == ""
    assert db.get_chat_project_default(SESSION_KEY) is None


@pytest.mark.asyncio
async def test_set_ambiguous_lists_candidates(runner_db):
    runner, db = runner_db
    projects.create_project("RNA Analysis")
    projects.create_project("RNA Structures")
    out = await runner._handle_project_command(_make_event("/project set rna"))
    assert "ambiguous" in out
    assert "rna-analysis" in out and "rna-structures" in out
    assert db.get_chat_project_default(SESSION_KEY) is None


@pytest.mark.asyncio
async def test_set_fuzzy_prefix_resolves(runner_db):
    runner, db = runner_db
    projects.create_project("Proteomics")
    db.create_session(SESSION_ID, "telegram")
    out = await runner._handle_project_command(_make_event("/project set prot"))
    assert "Proteomics" in out
    assert runner.session_store.entry.project_slug == "proteomics"


# ──────────────────────────────────────────────────────────────────────────
# set/new — empty conversation binds IN PLACE
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_on_empty_conversation_binds_in_place(runner_db):
    runner, db = runner_db
    p = projects.create_project("RNA")
    db.create_session(SESSION_ID, "telegram")  # message_count == 0

    out = await runner._handle_project_command(_make_event("/project set rna"))

    runner._handle_reset_command.assert_not_awaited()
    assert runner.session_store.entry.project_slug == "rna"
    assert db.get_chat_project_default(SESSION_KEY) == "rna"
    assert db.get_session(SESSION_ID)["project"] == "rna"
    overrides = tt.resolve_task_overrides(SESSION_ID)
    assert overrides.get("pin_cwd") is True
    assert overrides.get("cwd", "").startswith(p["cwd"])
    runner._evict_cached_agent.assert_called_with(SESSION_KEY)
    assert "Bound" in out and "rna" in out


@pytest.mark.asyncio
async def test_new_creates_and_binds(runner_db):
    runner, db = runner_db
    db.create_session(SESSION_ID, "telegram")
    out = await runner._handle_project_command(_make_event("/project new My Study"))
    assert "Created and bound" in out
    assert projects.get_project("my-study") is not None
    assert db.get_chat_project_default(SESSION_KEY) == "my-study"
    assert runner.session_store.entry.project_slug == "my-study"


@pytest.mark.asyncio
async def test_new_rejects_empty_name(runner_db):
    runner, _ = runner_db
    out = await runner._handle_project_command(_make_event("/project new"))
    assert "Usage" in out


# ──────────────────────────────────────────────────────────────────────────
# set — active conversation resets into a fresh bound session
# ──────────────────────────────────────────────────────────────────────────

def _add_activity(db, session_id):
    db.create_session(session_id, "telegram")
    db.append_message(session_id, "user", "hello")


@pytest.mark.asyncio
async def test_set_on_active_conversation_resets(runner_db):
    runner, db = runner_db
    projects.create_project("RNA")
    _add_activity(db, SESSION_ID)

    out = await runner._handle_project_command(_make_event("/project set rna"))

    runner._handle_reset_command.assert_awaited_once()
    # The successor session adopted the sticky default.
    assert runner.session_store.entry.session_id != SESSION_ID
    assert runner.session_store.entry.project_slug == "rna"
    assert db.get_chat_project_default(SESSION_KEY) == "rna"
    assert "fresh session" in out and "/resume" in out


# ──────────────────────────────────────────────────────────────────────────
# clear
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_when_unbound_is_noop(runner_db):
    runner, _ = runner_db
    out = await runner._handle_project_command(_make_event("/project clear"))
    assert "wasn't bound" in out


@pytest.mark.asyncio
async def test_clear_empty_conversation_unbinds_in_place(runner_db):
    runner, db = runner_db
    projects.create_project("RNA")
    db.create_session(SESSION_ID, "telegram")
    await runner._handle_project_command(_make_event("/project set rna"))

    out = await runner._handle_project_command(_make_event("/project clear"))

    runner._handle_reset_command.assert_not_awaited()
    assert runner.session_store.entry.project_slug == ""
    assert db.get_chat_project_default(SESSION_KEY) is None
    assert tt.resolve_task_overrides(SESSION_ID) == {}
    assert "unbound" in out


@pytest.mark.asyncio
async def test_clear_active_conversation_resets(runner_db):
    runner, db = runner_db
    projects.create_project("RNA")
    db.create_session(SESSION_ID, "telegram")
    await runner._handle_project_command(_make_event("/project set rna"))
    db.append_message(SESSION_ID, "user", "do things")

    out = await runner._handle_project_command(_make_event("/project clear"))

    runner._handle_reset_command.assert_awaited_once()
    assert db.get_chat_project_default(SESSION_KEY) is None
    assert runner.session_store.entry.project_slug == ""
    assert "fresh session" in out


# ──────────────────────────────────────────────────────────────────────────
# registry invariants
# ──────────────────────────────────────────────────────────────────────────

def test_project_command_registered_gateway_only():
    from hermes_cli.commands import resolve_command

    cmd = resolve_command("project")
    assert cmd is not None
    assert cmd.gateway_only is True
    assert set(cmd.subcommands) == {"list", "set", "new", "clear"}
