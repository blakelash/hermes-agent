"""Data model for the Prospective Scientific Memory Benchmark (PSMB).

This module defines the ground-truth structures for a benchmark *episode*: a
latent scientific world graph, the chronological artifacts that reveal fragments
of it, the latent connection opportunities hidden inside the trajectory, and the
ordinary scientific *tasks* used to probe whether an agent spontaneously surfaces
an old-but-now-relevant chain of evidence.

Design goals:
  * Zero dependency on the Hermes agent core -- generation and scoring must run
    standalone and fast (the runner imports the agent lazily).
  * Everything is JSON round-trippable so datasets are inspectable artifacts.
  * The ground truth retains the full evidence chain (per AgenticRAGTracer's
    critique that final-answer-only grading hides where reasoning broke), not
    just a concluding sentence.

The central unit is NOT a question/answer pair. It is a longitudinal trajectory
containing latent connection opportunities with explicit *maturation* windows.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "0.1.0"


class EntityType(str, Enum):
    COMPOUND = "compound"
    GENE = "gene"
    PROTEIN = "protein"
    PATHWAY = "pathway"
    PHENOTYPE = "phenotype"
    CELL_TYPE = "cell_type"
    ORGANISM = "organism"
    ASSAY = "assay"
    METABOLITE = "metabolite"
    CONDITION = "condition"
    PAPER = "paper"


class Predicate(str, Enum):
    BINDS = "binds"
    ACTIVATES = "activates"
    INHIBITS = "inhibits"
    PHENOCOPIES = "phenocopies"
    UPSTREAM_OF = "is_upstream_of"
    DOWNSTREAM_OF = "is_downstream_of"
    DEPENDS_ON = "depends_on"
    REGULATES = "regulates"
    EXPRESSED_IN = "is_expressed_in"
    ALTERS = "alters"
    LINKS = "links"
    REDUCES = "reduces"
    CONTRADICTS = "contradicts"
    SUPPORTS = "supports"


class EdgeStatus(str, Enum):
    OBSERVED = "observed"       # directly measured in an experiment
    INFERRED = "inferred"       # deduced by the agent/scientist
    PROPOSED = "proposed"       # hypothesis, not yet supported
    CONTRADICTED = "contradicted"  # later invalidated by contrary evidence


class Result(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NULL = "null"


class ArtifactKind(str, Enum):
    EXPERIMENT_REPORT = "experiment_report"
    MEETING_NOTE = "meeting_note"
    PAPER_SUMMARY = "paper_summary"
    FAILED_ANALYSIS = "failed_analysis"
    CODE_OUTPUT = "code_output"
    ASSAY_OBSERVATION = "assay_observation"
    USER_HYPOTHESIS = "user_hypothesis"
    COLLABORATOR_COMMENT = "collaborator_comment"
    DB_SEARCH = "db_search"


class ConnectionClass(str, Enum):
    MECHANISTIC_CHAIN = "mechanistic_chain"
    ANALOGICAL = "analogical"
    CONVERGENT_EVIDENCE = "convergent_evidence"
    CONTRADICTION_RESOLUTION = "contradiction_resolution"
    NEGATIVE_EVIDENCE = "negative_evidence"
    METHODOLOGICAL_TRANSFER = "methodological_transfer"
    SERENDIPITOUS_REPURPOSING = "serendipitous_repurposing"


class TaskKind(str, Enum):
    DESIGN_EXPERIMENT = "design_experiment"
    INTERPRET_RESULT = "interpret_result"
    PRIORITIZE = "prioritize"
    EXPLAIN_DISCREPANCY = "explain_discrepancy"
    CHOOSE_CONTROLS = "choose_controls"
    ASSESS_PAPER = "assess_paper"
    PROPOSE_MECHANISM = "propose_mechanism"
    # Diagnostic-only: an explicit memory question (NOT the central benchmark).
    EXPLICIT_RECALL = "explicit_recall"


@dataclass
class Entity:
    id: str
    type: EntityType
    name: str
    aliases: List[str] = field(default_factory=list)

    def all_names(self) -> List[str]:
        return [self.name, *self.aliases]


@dataclass
class Edge:
    """A typed, signed, conditional, provenance-carrying relationship.

    ``condition`` points at a CONDITION entity id and means "this edge only holds
    under that condition" -- the classic source of apparent contradictions that
    dissolve once dose/timing/species/isoform is accounted for.
    """

    id: str
    subject: str            # Entity.id
    predicate: Predicate
    object: str             # Entity.id
    sign: int = 1           # +1 activating/increasing, -1 inhibiting/reducing, 0 neutral
    condition: Optional[str] = None   # Entity.id (CONDITION) or None
    confidence: float = 1.0
    status: EdgeStatus = EdgeStatus.OBSERVED
    evidence_id: Optional[str] = None
    # Temporal validity, expressed in the episode's integer time units (months).
    known_from: int = 0
    invalidated_at: Optional[int] = None  # when contrary evidence arrived


@dataclass
class Evidence:
    id: str
    source: str             # Artifact.id or a paper Entity.id
    date: int               # episode time unit (month index)
    method: Optional[str] = None    # assay / analysis method
    result: Result = Result.POSITIVE
    confidence: float = 1.0
    provenance: str = ""    # free text pointer (who/where)


@dataclass
class Artifact:
    """A chronological fragment revealed to the agent during the trajectory.

    ``reveals_edges`` / ``reveals_entities`` are the ground-truth pieces of the
    world graph that this artifact discloses. The agent never sees these ids --
    only ``text`` -- but the scorer uses them to know what became *knowable*.
    """

    id: str
    time: int
    kind: ArtifactKind
    text: str
    reveals_edges: List[str] = field(default_factory=list)
    reveals_entities: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)


@dataclass
class Maturation:
    """Lifecycle of a latent connection, in episode time units.

    Separating these times is what distinguishes *memory* from *clairvoyance*:
    an agent gets no credit for blurting a connection before it is inferable
    (``solvable_time``) or acting on it before it is relevant (``useful_time``),
    and is penalized for surfacing an invalidated one (past ``expiry_time``).
    """

    birth_time: int                     # first relevant evidence appeared
    solvable_time: int                  # enough evidence existed to infer the chain
    useful_time: int                    # became relevant to an actual decision
    best_window: Tuple[int, int]        # (start, end) when surfacing helps most
    expiry_time: Optional[int] = None   # contrary evidence made it invalid


@dataclass
class Connection:
    """A latent connection opportunity: a chain through the world graph that only
    becomes actionable once the current task activates its far end."""

    id: str
    cls: ConnectionClass
    # Ordered edge ids forming the chain (the reasoning path).
    path_edges: List[str]
    # Minimal ground-truth support subgraph G* (entities + edges to recover).
    gold_entities: List[str]
    gold_edges: List[str]
    maturation: Maturation
    value: float = 1.0          # scientific value weight V_i in [0, inf)
    is_decoy: bool = False      # plausible but NOT a real/useful connection
    rationale: str = ""         # human-readable "why this matters"

    @property
    def hop_count(self) -> int:
        return len(self.path_edges)


@dataclass
class Task:
    """An ordinary scientific task used as a probe.

    Crucially, ``prompt`` does NOT name the old facts or ask the agent to recall
    anything. It poses a normal decision (design the next experiment, interpret a
    result, ...). The agent is expected to *spontaneously* surface
    ``target_connection_id`` if its memory works prospectively.
    """

    id: str
    time: int
    kind: TaskKind
    prompt: str
    target_connection_id: Optional[str] = None
    # Connections that would be WRONG/irrelevant to raise now (precision test).
    distractor_connection_ids: List[str] = field(default_factory=list)
    explicit: bool = False      # True only for diagnostic EXPLICIT_RECALL probes


@dataclass
class DifficultyProfile:
    """Difficulty is more than hop count (per the benchmark spec)."""

    hop_count: int = 0
    semantic_distance: float = 0.0      # 0 = shared entity names, 1 = fully disjoint
    time_distance: int = 0              # months between first and last evidence
    entity_ambiguity: float = 0.0       # synonym/orthology resolution required
    conditionality: float = 0.0         # fraction of edges gated by a condition
    distractor_density: float = 0.0
    evidence_uncertainty: float = 0.0   # mean (1 - confidence) over gold edges

    def score(self) -> float:
        """A single scalar for coarse stratification (higher = harder)."""
        return round(
            0.9 * self.hop_count
            + 1.5 * self.semantic_distance
            + 0.02 * self.time_distance
            + 1.2 * self.entity_ambiguity
            + 1.3 * self.conditionality
            + 1.0 * self.distractor_density
            + 1.4 * self.evidence_uncertainty,
            4,
        )


@dataclass
class Episode:
    id: str
    seed: int
    entities: List[Entity]
    edges: List[Edge]
    evidence: List[Evidence]
    artifacts: List[Artifact]       # time-ordered
    connections: List[Connection]
    tasks: List[Task]               # time-ordered
    difficulty: DifficultyProfile = field(default_factory=DifficultyProfile)
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    # -- lookups -------------------------------------------------------------
    def entity(self, eid: str) -> Entity:
        return self._entity_index()[eid]

    def edge(self, eid: str) -> Edge:
        return self._edge_index()[eid]

    def connection(self, cid: str) -> Connection:
        return self._connection_index()[cid]

    def _entity_index(self) -> Dict[str, Entity]:
        return {e.id: e for e in self.entities}

    def _edge_index(self) -> Dict[str, Edge]:
        return {e.id: e for e in self.edges}

    def _connection_index(self) -> Dict[str, Connection]:
        return {c.id: c for c in self.connections}

    def timeline(self) -> List[int]:
        times = {a.time for a in self.artifacts} | {t.time for t in self.tasks}
        return sorted(times)

    def artifacts_at(self, t: int) -> List[Artifact]:
        return [a for a in self.artifacts if a.time == t]

    def tasks_at(self, t: int) -> List[Task]:
        return [tk for tk in self.tasks if tk.time == t]

    # -- (de)serialization ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return _dump(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=_enum_default)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Episode":
        return _load_episode(d)

    @staticmethod
    def from_json(s: str) -> "Episode":
        return Episode.from_dict(json.loads(s))


# --------------------------------------------------------------------------- #
# (de)serialization helpers -- keep enums as their string values in JSON.
# --------------------------------------------------------------------------- #
def _enum_default(o: Any) -> Any:
    if isinstance(o, Enum):
        return o.value
    raise TypeError(f"not JSON serializable: {type(o)!r}")


def _dump(obj: Any) -> Any:
    d = asdict(obj)
    return _coerce_enums(d)


def _coerce_enums(x: Any) -> Any:
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, dict):
        return {k: _coerce_enums(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_coerce_enums(v) for v in x]
    return x


def _load_episode(d: Dict[str, Any]) -> Episode:
    entities = [Entity(id=e["id"], type=EntityType(e["type"]), name=e["name"],
                       aliases=list(e.get("aliases", []))) for e in d["entities"]]
    edges = [Edge(
        id=e["id"], subject=e["subject"], predicate=Predicate(e["predicate"]),
        object=e["object"], sign=int(e.get("sign", 1)), condition=e.get("condition"),
        confidence=float(e.get("confidence", 1.0)),
        status=EdgeStatus(e.get("status", "observed")),
        evidence_id=e.get("evidence_id"), known_from=int(e.get("known_from", 0)),
        invalidated_at=e.get("invalidated_at"),
    ) for e in d["edges"]]
    evidence = [Evidence(
        id=v["id"], source=v["source"], date=int(v["date"]), method=v.get("method"),
        result=Result(v.get("result", "positive")),
        confidence=float(v.get("confidence", 1.0)), provenance=v.get("provenance", ""),
    ) for v in d.get("evidence", [])]
    artifacts = [Artifact(
        id=a["id"], time=int(a["time"]), kind=ArtifactKind(a["kind"]), text=a["text"],
        reveals_edges=list(a.get("reveals_edges", [])),
        reveals_entities=list(a.get("reveals_entities", [])),
        evidence_ids=list(a.get("evidence_ids", [])),
    ) for a in d.get("artifacts", [])]
    connections = []
    for c in d.get("connections", []):
        m = c["maturation"]
        connections.append(Connection(
            id=c["id"], cls=ConnectionClass(c["cls"]), path_edges=list(c["path_edges"]),
            gold_entities=list(c["gold_entities"]), gold_edges=list(c["gold_edges"]),
            maturation=Maturation(
                birth_time=int(m["birth_time"]), solvable_time=int(m["solvable_time"]),
                useful_time=int(m["useful_time"]),
                best_window=tuple(m["best_window"]),  # type: ignore[arg-type]
                expiry_time=m.get("expiry_time"),
            ),
            value=float(c.get("value", 1.0)), is_decoy=bool(c.get("is_decoy", False)),
            rationale=c.get("rationale", ""),
        ))
    tasks = [Task(
        id=t["id"], time=int(t["time"]), kind=TaskKind(t["kind"]), prompt=t["prompt"],
        target_connection_id=t.get("target_connection_id"),
        distractor_connection_ids=list(t.get("distractor_connection_ids", [])),
        explicit=bool(t.get("explicit", False)),
    ) for t in d.get("tasks", [])]
    dp = d.get("difficulty", {})
    difficulty = DifficultyProfile(
        hop_count=int(dp.get("hop_count", 0)),
        semantic_distance=float(dp.get("semantic_distance", 0.0)),
        time_distance=int(dp.get("time_distance", 0)),
        entity_ambiguity=float(dp.get("entity_ambiguity", 0.0)),
        conditionality=float(dp.get("conditionality", 0.0)),
        distractor_density=float(dp.get("distractor_density", 0.0)),
        evidence_uncertainty=float(dp.get("evidence_uncertainty", 0.0)),
    )
    return Episode(
        id=d["id"], seed=int(d.get("seed", 0)), entities=entities, edges=edges,
        evidence=evidence, artifacts=artifacts, connections=connections, tasks=tasks,
        difficulty=difficulty, schema_version=d.get("schema_version", SCHEMA_VERSION),
        notes=d.get("notes", ""),
    )
