"""Scoring for PSMB.

Four scored layers, per the design:

  A. Memory integrity   -- did the system retain the right atomic evidence?
  B. Connection recovery-- given an opportunity, did it recover the support chain?
  C. Trigger quality    -- did it surface the connection, at the right time, without
                           burying the user in false/low-value injections? (novel)
  D. Downstream utility -- Memory Value = utility(with memory) - utility(without).

Detection is deterministic and structural (entity/edge mention over the
ground-truth minimal subgraph), NOT "ask an LLM whether the prose sounds right."
This makes scores hard to game and fully offline. The mention detectors are
pluggable so a stricter NLI/LLM-judge can be swapped in later without changing
the metric definitions.

Key idea for *spontaneity*: recall is only counted over gold entities that are
NOT already present in the turn's stimulus. Naming something the prompt just
mentioned is not prospective recall.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from benchmarks.prospective_scientific_memory.schema import (
    Connection,
    Entity,
    Episode,
    Task,
)

# --------------------------------------------------------------------------- #
# Turn records produced by the runner (kept independent of the agent core).
# --------------------------------------------------------------------------- #
@dataclass
class TurnRecord:
    time: int
    kind: str                       # "artifact" | "task"
    stimulus_text: str              # what was shown to the agent this turn
    response_text: str              # the agent's reply
    task_id: Optional[str] = None
    target_connection_id: Optional[str] = None
    is_explicit: bool = False


@dataclass
class RunLog:
    episode_id: str
    memory_mode: str
    turns: List[TurnRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"episode_id": self.episode_id, "memory_mode": self.memory_mode,
                "turns": [asdict(t) for t in self.turns]}

    @staticmethod
    def from_dict(d: dict) -> "RunLog":
        return RunLog(episode_id=d["episode_id"], memory_mode=d["memory_mode"],
                      turns=[TurnRecord(**t) for t in d.get("turns", [])])


# --------------------------------------------------------------------------- #
# Mention detection
# --------------------------------------------------------------------------- #
def name_variants(entity: Entity) -> List[str]:
    """All strings that count as naming this entity.

    Includes canonical name, aliases, and for parenthetical names like
    ``pathway A (NRF2)`` both ``pathway A`` and ``NRF2``.
    """
    out: Set[str] = set()
    for nm in entity.all_names():
        nm = nm.strip()
        if not nm:
            continue
        out.add(nm)
        m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", nm)
        if m:
            head, inner = m.group(1).strip(), m.group(2).strip()
            if head:
                out.add(head)
            if inner:
                out.add(inner)
    # Drop ultra-short/ambiguous tokens (single letters) unless they are the
    # only variant -- they cause false positives ("M", "P", "A").
    variants = [v for v in out if len(v) >= 3]
    return variants or list(out)


def _mk_pattern(variant: str) -> re.Pattern:
    esc = re.escape(variant)
    # word-ish boundaries: not preceded/followed by an alphanumeric
    return re.compile(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])", re.IGNORECASE)


def mentioned(text: str, entity: Entity) -> bool:
    for v in name_variants(entity):
        if _mk_pattern(v).search(text):
            return True
    return False


EntityDetector = Callable[[str, Entity], bool]


def detect_entities(text: str, entities: Sequence[Entity],
                    detector: EntityDetector = mentioned) -> Set[str]:
    return {e.id for e in entities if detector(text, e)}


# --------------------------------------------------------------------------- #
# Connection recovery (Layer B) for one response
# --------------------------------------------------------------------------- #
@dataclass
class ConnectionRecovery:
    connection_id: str
    entity_recall: float
    entity_precision_proxy: float
    edge_recall: float
    edge_precision_proxy: float
    detected_gold_entities: List[str]
    detected_gold_edges: List[str]
    spontaneous_recall: float           # over gold entities NOT in the stimulus


def _gold_entity_objs(ep: Episode, conn: Connection) -> List[Entity]:
    idx = {e.id: e for e in ep.entities}
    return [idx[e] for e in conn.gold_entities if e in idx]


def _edge_recovered(text: str, ep: Episode, edge_id: str,
                    detector: EntityDetector) -> bool:
    """Proxy: an edge is 'recovered' if BOTH endpoint entities are named in the
    response. Documented as lenient; swap in a relational detector for rigor."""
    idx = {e.id: e for e in ep.entities}
    e = ep.edge(edge_id)
    s, o = idx.get(e.subject), idx.get(e.object)
    if not s or not o:
        return False
    return detector(text, s) and detector(text, o)


def score_connection_recovery(
    ep: Episode, conn: Connection, response: str, *, stimulus: str = "",
    detector: EntityDetector = mentioned,
    decoy_entity_ids: Optional[Set[str]] = None,
    decoy_edge_ids: Optional[Sequence[str]] = None,
) -> ConnectionRecovery:
    gold_ents = _gold_entity_objs(ep, conn)
    detected = {e.id for e in gold_ents if detector(response, e)}
    entity_recall = len(detected) / max(1, len(gold_ents))

    # precision proxy: gold vs decoy entities recovered
    decoy_entity_ids = decoy_entity_ids or set()
    idx = {e.id: e for e in ep.entities}
    decoy_hits = {i for i in decoy_entity_ids if i in idx and detector(response, idx[i])}
    entity_precision = (len(detected) / max(1, len(detected) + len(decoy_hits)))

    rec_edges = [x for x in conn.gold_edges
                 if _edge_recovered(response, ep, x, detector)]
    edge_recall = len(rec_edges) / max(1, len(conn.gold_edges))
    decoy_edge_ids = list(decoy_edge_ids or [])
    decoy_edge_hits = [x for x in decoy_edge_ids
                       if _edge_recovered(response, ep, x, detector)]
    edge_precision = len(rec_edges) / max(1, len(rec_edges) + len(decoy_edge_hits))

    # spontaneity: gold entities NOT present in the stimulus
    stim_ids = {e.id for e in gold_ents if detector(stimulus, e)} if stimulus else set()
    spont_gold = [e for e in gold_ents if e.id not in stim_ids]
    spont_detected = [e for e in spont_gold if e.id in detected]
    spont_recall = len(spont_detected) / max(1, len(spont_gold))

    return ConnectionRecovery(
        connection_id=conn.id, entity_recall=round(entity_recall, 4),
        entity_precision_proxy=round(entity_precision, 4),
        edge_recall=round(edge_recall, 4),
        edge_precision_proxy=round(edge_precision, 4),
        detected_gold_entities=sorted(detected),
        detected_gold_edges=list(rec_edges),
        spontaneous_recall=round(spont_recall, 4))


# --------------------------------------------------------------------------- #
# Trigger quality (Layer C) + timeliness
# --------------------------------------------------------------------------- #
def _timeliness(conn: Connection, t: int) -> float:
    m = conn.maturation
    if t < m.solvable_time:
        return 0.0                      # premature: no credit
    if m.expiry_time is not None and t >= m.expiry_time:
        return 0.0                      # stale: no credit
    lo, hi = m.best_window
    if lo <= t <= hi:
        return 1.0
    # linear decay outside the best window but within [solvable, expiry)
    if t < lo:
        span = max(1, lo - m.solvable_time)
        return round(max(0.4, 1.0 - (lo - t) / span * 0.6), 4)
    far = t - hi
    return round(max(0.2, 1.0 - far * 0.2), 4)


def _is_surfaced(rec: ConnectionRecovery, *, surface_threshold: float = 0.5,
                 require_anchor: Optional[bool] = None,
                 anchor_detected: bool = False) -> bool:
    """A connection is 'surfaced' when the agent spontaneously names enough of
    its chain (over entities not in the stimulus). If an old-anchor is defined,
    require it -- recalling the far-past fact is the crux."""
    ok = rec.spontaneous_recall >= surface_threshold
    if require_anchor:
        return ok and anchor_detected
    return ok


@dataclass
class TriggerQuality:
    opportunity_recall: float
    intervention_precision: float
    mean_timeliness: float
    false_injections: int
    interruption_burden: int
    redundancy: int
    mean_latency: float
    pmu: float                          # Prospective Memory Utility
    details: List[dict] = field(default_factory=list)


def _anchor_entity_id(conn: Connection) -> Optional[str]:
    for eid in conn.gold_entities:
        if "oldx" in eid or eid == "x":
            return eid
    return None


def score_trigger_quality(
    ep: Episode, run: RunLog, *, detector: EntityDetector = mentioned,
    surface_threshold: float = 0.5, lam: float = 0.5, gamma: float = 0.3,
) -> TriggerQuality:
    conns = {c.id: c for c in ep.connections}
    decoy_ids = {c.id for c in ep.connections if c.is_decoy}
    decoy_ent_ids: Set[str] = set()
    decoy_edge_ids: List[str] = []
    for c in ep.connections:
        if c.is_decoy:
            decoy_ent_ids |= set(c.gold_entities)
            decoy_edge_ids += list(c.gold_edges)

    idx = {e.id: e for e in ep.entities}
    tasks_by_time = {t.time: t for t in ep.tasks}

    surfaced_first: Dict[str, int] = {}      # connection -> first surfacing time
    surfaced_count: Dict[str, int] = {}
    false_injections = 0
    interruption_burden = 0
    details: List[dict] = []

    # Walk every turn; detect which connections were surfaced.
    for turn in run.turns:
        for c in ep.connections:
            if c.is_decoy:
                # decoy surfacing = false/interruption regardless of timing
                rec = score_connection_recovery(
                    ep, c, turn.response_text, stimulus=turn.stimulus_text,
                    detector=detector)
                if _is_surfaced(rec, surface_threshold=surface_threshold):
                    false_injections += 1
                    if turn.kind != "task":
                        interruption_burden += 1
                    details.append({"time": turn.time, "connection": c.id,
                                    "event": "decoy_surfaced"})
                continue
            anchor = _anchor_entity_id(c)
            anchor_ok = bool(anchor and anchor in idx
                             and detector(turn.response_text, idx[anchor]))
            rec = score_connection_recovery(
                ep, c, turn.response_text, stimulus=turn.stimulus_text,
                detector=detector, decoy_entity_ids=decoy_ent_ids,
                decoy_edge_ids=decoy_edge_ids)
            if _is_surfaced(rec, surface_threshold=surface_threshold,
                            require_anchor=bool(anchor), anchor_detected=anchor_ok):
                surfaced_count[c.id] = surfaced_count.get(c.id, 0) + 1
                if c.id not in surfaced_first:
                    surfaced_first[c.id] = turn.time
                # premature or stale => false injection
                m = c.maturation
                stale = m.expiry_time is not None and turn.time >= m.expiry_time
                premature = turn.time < m.solvable_time
                if premature or stale:
                    false_injections += 1
                if turn.kind != "task" and (premature or turn.time < m.useful_time):
                    interruption_burden += 1
                details.append({"time": turn.time, "connection": c.id,
                                "event": "surfaced", "premature": premature,
                                "stale": stale, "spont_recall": rec.spontaneous_recall,
                                "edge_recall": rec.edge_recall})

    # Opportunity recall / timeliness / PMU are computed over real (non-decoy,
    # non-explicit) task opportunities.
    opp_total = 0
    opp_hit = 0
    timeliness_vals: List[float] = []
    latencies: List[float] = []
    pmu = 0.0
    for task in ep.tasks:
        if task.explicit or not task.target_connection_id:
            continue
        c = conns.get(task.target_connection_id)
        if not c or c.is_decoy:
            continue
        opp_total += 1
        m = c.maturation
        # Opportunity recall is about the DECISION moment: did the agent surface
        # the connection at the task turn, within the valid [solvable, expiry)
        # window? Premature blurts at earlier turns are penalized separately
        # (false injections / interruption / precision), not here -- so a
        # spray-everything agent keeps high recall but tanks PMU and precision.
        task_turn = next((tr for tr in run.turns if tr.task_id == task.id), None)
        resp = task_turn.response_text if task_turn else ""
        stim = task_turn.stimulus_text if task_turn else ""
        rec = score_connection_recovery(ep, c, resp, stimulus=stim, detector=detector)
        anchor = _anchor_entity_id(c)
        anchor_ok = bool(anchor and anchor in idx and detector(resp, idx[anchor]))
        surfaced_at_task = _is_surfaced(
            rec, surface_threshold=surface_threshold,
            require_anchor=bool(anchor), anchor_detected=anchor_ok)
        in_window = task_turn is not None and task_turn.time >= m.solvable_time and (
            m.expiry_time is None or task_turn.time < m.expiry_time)
        if surfaced_at_task and in_window:
            opp_hit += 1
            T = _timeliness(c, task_turn.time)
            timeliness_vals.append(T)
            R = 0.5 * rec.edge_recall + 0.5 * rec.spontaneous_recall
            pmu += c.value * R * T
        else:
            timeliness_vals.append(0.0)
        first_t = surfaced_first.get(c.id)
        if first_t is not None:
            latencies.append(max(0, first_t - m.solvable_time))

    pmu = pmu - lam * false_injections - gamma * interruption_burden
    opportunity_recall = opp_hit / max(1, opp_total)
    # intervention precision: appropriate surfacings / all surfacings
    appropriate = opp_hit
    all_surfacings = sum(surfaced_count.values()) + false_injections
    intervention_precision = appropriate / max(1, all_surfacings)
    redundancy = sum(max(0, v - 1) for v in surfaced_count.values())

    return TriggerQuality(
        opportunity_recall=round(opportunity_recall, 4),
        intervention_precision=round(intervention_precision, 4),
        mean_timeliness=round(sum(timeliness_vals) / max(1, len(timeliness_vals)), 4),
        false_injections=false_injections,
        interruption_burden=interruption_burden,
        redundancy=redundancy,
        mean_latency=round(sum(latencies) / max(1, len(latencies)), 4),
        pmu=round(pmu, 4), details=details)


# --------------------------------------------------------------------------- #
# Memory integrity (Layer A) -- scored on the explicit diagnostic probe
# --------------------------------------------------------------------------- #
@dataclass
class MemoryIntegrity:
    evidence_recall: float
    condition_fidelity: float
    invalidated_suppression: float
    n_probes: int


def score_memory_integrity(ep: Episode, run: RunLog, *,
                           detector: EntityDetector = mentioned) -> MemoryIntegrity:
    idx = {e.id: e for e in ep.entities}
    recalls: List[float] = []
    cond_scores: List[float] = []
    supp_scores: List[float] = []
    n = 0
    for task in ep.tasks:
        if not task.explicit or not task.target_connection_id:
            continue
        n += 1
        c = ep.connection(task.target_connection_id)
        turn = next((tr for tr in run.turns if tr.task_id == task.id), None)
        resp = turn.response_text if turn else ""
        gold_ents = [idx[e] for e in c.gold_entities if e in idx]
        detected = [e for e in gold_ents if detector(resp, e)]
        recalls.append(len(detected) / max(1, len(gold_ents)))
        # condition fidelity: for each conditional gold edge whose endpoints were
        # recalled, is the condition also named?
        cond_ok, cond_tot = 0, 0
        for eid in c.gold_edges:
            e = ep.edge(eid)
            if e.condition and e.condition in idx:
                cond_tot += 1
                if detector(resp, idx[e.condition]):
                    cond_ok += 1
        cond_scores.append(cond_ok / cond_tot if cond_tot else 1.0)
        # invalidated suppression: agent should NOT assert a contradicted edge
        inv_edges = [ep.edge(eid) for eid in c.gold_edges
                     if ep.edge(eid).invalidated_at is not None]
        if inv_edges:
            bad = sum(1 for e in inv_edges
                      if _edge_recovered(resp, ep, e.id, detector))
            supp_scores.append(1.0 - bad / len(inv_edges))
    return MemoryIntegrity(
        evidence_recall=round(sum(recalls) / max(1, len(recalls)), 4),
        condition_fidelity=round(sum(cond_scores) / max(1, len(cond_scores)), 4),
        invalidated_suppression=round(
            sum(supp_scores) / len(supp_scores), 4) if supp_scores else 1.0,
        n_probes=n)


# --------------------------------------------------------------------------- #
# Episode-level roll-up
# --------------------------------------------------------------------------- #
@dataclass
class EpisodeScore:
    episode_id: str
    memory_mode: str
    integrity: MemoryIntegrity
    trigger: TriggerQuality
    task_utility: float                 # for Memory Value (Layer D)
    connection_recovery_at_tasks: List[ConnectionRecovery]

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id, "memory_mode": self.memory_mode,
            "integrity": asdict(self.integrity), "trigger": asdict(self.trigger),
            "task_utility": self.task_utility,
            "connection_recovery_at_tasks": [asdict(c)
                                             for c in self.connection_recovery_at_tasks],
        }


def score_episode(ep: Episode, run: RunLog, *,
                  detector: EntityDetector = mentioned,
                  surface_threshold: float = 0.5) -> EpisodeScore:
    trigger = score_trigger_quality(ep, run, detector=detector,
                                    surface_threshold=surface_threshold)
    integrity = score_memory_integrity(ep, run, detector=detector)
    recs: List[ConnectionRecovery] = []
    utils: List[float] = []
    for task in ep.tasks:
        if task.explicit or not task.target_connection_id:
            continue
        c = ep.connection(task.target_connection_id)
        turn = next((tr for tr in run.turns if tr.task_id == task.id), None)
        resp = turn.response_text if turn else ""
        stim = turn.stimulus_text if turn else ""
        rec = score_connection_recovery(ep, c, resp, stimulus=stim, detector=detector)
        recs.append(rec)
        # task utility = blend of spontaneous recall + edge recall at the decision
        utils.append(round(0.6 * rec.spontaneous_recall + 0.4 * rec.edge_recall, 4))
    task_utility = round(sum(utils) / max(1, len(utils)), 4)
    return EpisodeScore(
        episode_id=ep.id, memory_mode=run.memory_mode, integrity=integrity,
        trigger=trigger, task_utility=task_utility,
        connection_recovery_at_tasks=recs)


# --------------------------------------------------------------------------- #
# Downstream utility / Memory Value (Layer D)
# --------------------------------------------------------------------------- #
def memory_value(with_mem: EpisodeScore, without_mem: EpisodeScore) -> float:
    """The honest test: how much did memory change what the agent produced?"""
    return round(with_mem.task_utility - without_mem.task_utility, 4)


def pairwise_memory_value(scores_by_mode: Dict[str, List[EpisodeScore]],
                          *, with_mode: str, without_mode: str) -> Dict[str, float]:
    """Compute mean Memory Value across a dataset for a (with, without) pair."""
    a = {s.episode_id: s for s in scores_by_mode.get(with_mode, [])}
    b = {s.episode_id: s for s in scores_by_mode.get(without_mode, [])}
    common = sorted(set(a) & set(b))
    vals = [memory_value(a[k], b[k]) for k in common]
    return {
        "with_mode": with_mode, "without_mode": without_mode,
        "n": len(common),
        "mean_memory_value": round(sum(vals) / max(1, len(vals)), 4),
        "mean_utility_with": round(
            sum(a[k].task_utility for k in common) / max(1, len(common)), 4),
        "mean_utility_without": round(
            sum(b[k].task_utility for k in common) / max(1, len(common)), 4),
    }
