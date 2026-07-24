"""Drive an agent through a PSMB episode and produce a RunLog for scoring.

The runner is agent-agnostic: it talks to an ``AgentClient``. Two clients ship:

  * ``HermesAgentClient`` -- wraps the real Hermes ``AIAgent`` (lazy-imported so
    generation/scoring stay dependency-free). Memory is realized purely through
    context carried per the memory mode, so ``no_memory`` vs ``full_context``
    gives an honest Memory-Value regret with no external memory server.
  * ``CallableAgentClient`` -- wraps any ``fn(messages) -> str`` (used by the
    offline mock and by tests to validate the harness end-to-end).

Turn protocol: artifacts are presented as chronological lab updates (so premature
/ interrupting recall can be detected), and tasks are presented as ordinary
scientific requests that never name the old facts.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional, Protocol

from benchmarks.prospective_scientific_memory.memory_modes import ModeSpec, clip_history
from benchmarks.prospective_scientific_memory.schema import (
    Artifact,
    Episode,
    Task,
    TaskKind,
)
from benchmarks.prospective_scientific_memory.scoring import RunLog, TurnRecord

_ARTIFACT_SYS = (
    "You are a scientific research collaborator keeping up with an ongoing "
    "project. You receive lab updates over time and are later asked to help with "
    "decisions. When something new connects to earlier work, say so proactively."
)

_ARTIFACT_TEMPLATE = "Lab update (month {t}, {kind}): {text}"
_TASK_TEMPLATE = "{prompt}"


class AgentClient(Protocol):
    def begin(self, episode: Episode, mode: ModeSpec) -> None: ...
    def respond(self, stimulus: str, *, is_task: bool, time: int) -> str: ...
    def finish(self) -> None: ...


# --------------------------------------------------------------------------- #
# Callable-backed client (offline mock / tests)
# --------------------------------------------------------------------------- #
class CallableAgentClient:
    """Wrap ``fn(messages: list[dict]) -> str`` with memory-mode history."""

    def __init__(self, fn: Callable[[List[dict]], str], system: str = _ARTIFACT_SYS):
        self.fn = fn
        self.system = system
        self._history: List[dict] = []
        self.mode = ModeSpec(name="no_memory")

    def begin(self, episode: Episode, mode: ModeSpec) -> None:
        self.mode = mode
        self._history = []

    def respond(self, stimulus: str, *, is_task: bool, time: int) -> str:
        ctx = clip_history(self._history, self.mode) or []
        messages = [{"role": "system", "content": self.system}, *ctx,
                    {"role": "user", "content": stimulus}]
        reply = self.fn(messages)
        # grow the canonical (full) history regardless of mode; clip_history
        # decides how much is *shown* next turn.
        self._history.append({"role": "user", "content": stimulus})
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def finish(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# Hermes-backed client
# --------------------------------------------------------------------------- #
class HermesAgentClient:
    """Drive the real Hermes AIAgent. Lazy-imports the heavy agent stack."""

    def __init__(self, *, model: str, base_url: str, api_key: str = "psmb",
                 provider: str = "custom", toolsets: Optional[List[str]] = None,
                 hermes_home: Optional[str] = None, max_iterations: int = 12,
                 use_provider_memory: bool = False):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.provider = provider
        self.toolsets = toolsets or ["safe"]  # no terminal by default
        self.hermes_home = hermes_home
        self.max_iterations = max_iterations
        self.use_provider_memory = use_provider_memory
        self._agent = None
        self._history: List[dict] = []
        self.mode = ModeSpec(name="no_memory")
        self._turn = 0

    def begin(self, episode: Episode, mode: ModeSpec) -> None:
        if self.hermes_home:
            os.environ["HERMES_HOME"] = self.hermes_home
        os.environ.setdefault("HERMES_YOLO_MODE", "1")
        from hermes_state import SessionDB
        from run_agent import AIAgent

        self.mode = mode
        self._history = []
        self._turn = 0
        db = SessionDB()
        self._agent = AIAgent(
            provider=self.provider, base_url=self.base_url, api_key=self.api_key,
            model=self.model, enabled_toolsets=self.toolsets,
            skip_context_files=True,
            skip_memory=not self.use_provider_memory,
            quiet_mode=True, session_db=db,
            session_id=f"psmb_{episode.id}_{mode.name}",
            max_iterations=self.max_iterations,
        )

    def respond(self, stimulus: str, *, is_task: bool, time: int) -> str:
        assert self._agent is not None, "call begin() first"
        history = clip_history(self._history, self.mode)
        result = self._agent.run_conversation(
            user_message=stimulus, conversation_history=history,
            task_id=f"psmb_turn_{self._turn}")
        self._turn += 1
        self._history = result.get("messages", self._history)
        return result.get("final_response") or ""

    def finish(self) -> None:
        if self._agent is not None:
            try:
                self._agent.shutdown_memory_provider()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# The runner
# --------------------------------------------------------------------------- #
def run_episode(episode: Episode, client: AgentClient, *, mode: str = "full_context",
                include_artifacts: bool = True) -> RunLog:
    spec = ModeSpec.parse(mode)
    client.begin(episode, spec)
    run = RunLog(episode_id=episode.id, memory_mode=mode)
    try:
        for t in episode.timeline():
            if include_artifacts:
                for art in episode.artifacts_at(t):
                    stim = _ARTIFACT_TEMPLATE.format(
                        t=art.time, kind=art.kind.value, text=art.text)
                    reply = client.respond(stim, is_task=False, time=t)
                    run.turns.append(TurnRecord(
                        time=t, kind="artifact", stimulus_text=art.text,
                        response_text=reply))
            for task in episode.tasks_at(t):
                stim = _TASK_TEMPLATE.format(prompt=task.prompt)
                reply = client.respond(stim, is_task=True, time=t)
                run.turns.append(TurnRecord(
                    time=t, kind="task", stimulus_text=task.prompt,
                    response_text=reply, task_id=task.id,
                    target_connection_id=task.target_connection_id,
                    is_explicit=task.explicit))
    finally:
        client.finish()
    return run
