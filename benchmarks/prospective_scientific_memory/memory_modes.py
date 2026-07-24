"""Memory-mode policies for PSMB baselines.

The benchmark's whole point is to compare memory architectures, so the runner is
parameterized by a *memory mode*. The prototype ships the two anchors needed to
compute Memory Value (Layer D) end-to-end with no external services:

  * ``no_memory``   -- each turn is independent (lower bound / control).
  * ``full_context``-- the entire trajectory is carried in-context (upper bound).

Plus stubs/extension points for the fuller baseline ladder from the design
(sliding window, vector RAG, temporal KG, provider-backed, oracle, ...). Modes
that require external infrastructure raise a clear NotImplementedError so callers
know they are not part of the initial prototype.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# Modes fully implemented in the prototype (offline, no external services).
IMPLEMENTED_MODES = ("no_memory", "full_context")

# Modes named in the design but left as extension points.
PLANNED_MODES = (
    "sliding_window",       # last-k turns
    "vector_rag",           # embedding retrieval over the episodic ledger
    "iterative_rag",        # multi-hop retrieve-read loop
    "temporal_kg",          # temporal knowledge graph
    "graph_plus_ledger",    # KG + immutable episodic store
    "provider",             # a configured Hermes memory provider (e.g. hindsight)
    "always_on",            # inject the whole memory bank every turn
    "selective_proactive",  # a separate memory agent that decides to intervene
    "oracle",               # ground-truth retrieval (upper bound on retrieval)
)


@dataclass
class ModeSpec:
    name: str
    window: Optional[int] = None    # for sliding_window

    @staticmethod
    def parse(spec: str) -> "ModeSpec":
        if ":" in spec:
            base, arg = spec.split(":", 1)
            if base == "sliding_window":
                return ModeSpec(name=base, window=int(arg))
            raise ValueError(f"mode {base!r} takes no argument")
        return ModeSpec(name=spec)


def is_implemented(spec: str) -> bool:
    return ModeSpec.parse(spec).name in IMPLEMENTED_MODES


def clip_history(history: List[dict], mode: ModeSpec) -> Optional[List[dict]]:
    """Return the conversation history to feed for this turn.

    ``None`` means 'no prior context'. For ``full_context`` we return everything;
    for ``no_memory`` we return None; for ``sliding_window`` the last-k exchanges.
    """
    if mode.name == "no_memory":
        return None
    if mode.name == "full_context":
        return history or None
    if mode.name == "sliding_window":
        k = mode.window or 3
        # keep the last k user/assistant exchanges (2 messages each, roughly)
        return history[-2 * k:] or None
    raise NotImplementedError(
        f"memory mode {mode.name!r} is a planned extension point, not implemented "
        f"in the prototype (implemented: {IMPLEMENTED_MODES})")
