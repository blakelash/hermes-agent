"""Compose PSMB episodes from latent chains and write datasets to disk.

An episode weaves one or more latent connection chains onto an absolute
timeline, scrambles the reveal order, sprinkles in unrelated noise artifacts and
decoys, adds an *old anchor* fact (the far-past result the agent must
spontaneously recall), and finally poses ordinary scientific tasks at the moment
each connection matures -- without ever naming the old facts.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

from benchmarks.prospective_scientific_memory.schema import (
    Artifact,
    ArtifactKind,
    Connection,
    ConnectionClass,
    DifficultyProfile,
    Edge,
    EdgeStatus,
    Entity,
    EntityType,
    Episode,
    Evidence,
    Maturation,
    Predicate,
    Result,
    Task,
    TaskKind,
)
from benchmarks.prospective_scientific_memory.world import build_chain, build_decoy

_TASK_TEMPLATES = {
    TaskKind.PROPOSE_MECHANISM: (
        "We're actively working on {near}. In a fresh {assay} run it produced an "
        "unexpected effect on {near_obj}. Propose plausible mechanisms for this "
        "phenotype and note anything from our own past work that might be relevant."
    ),
    TaskKind.DESIGN_EXPERIMENT: (
        "I'm designing the next study around {near}. What experiment should we run "
        "next, and are there prior results in this project we should account for?"
    ),
    TaskKind.INTERPRET_RESULT: (
        "New result just landed: {near} shifts {near_obj}. Help me interpret what "
        "this could mean mechanistically for the project."
    ),
    TaskKind.PRIORITIZE: (
        "We can only follow up on a few compounds this quarter and {near} is on the "
        "list. How should we prioritize, given everything we've seen so far?"
    ),
    TaskKind.EXPLAIN_DISCREPANCY: (
        "There's a discrepancy: {near} looks active in one assay but not another. "
        "How would you explain it?"
    ),
}

_NOISE_ARTIFACTS = [
    "Freezer inventory reconciled; two aliquots of the {x} stock relabeled.",
    "Instrument QC passed for the {x} plate reader; no drift.",
    "Reminder: {x} grant progress report due end of month.",
    "Onboarding note: new RA trained on {x} tissue culture protocol.",
    "Vendor backorder on {x} antibody; substitute clone ordered.",
]


def _noise(rng: random.Random, t: int, i: int) -> Artifact:
    stem = rng.choice(["CD8", "melanoma", "fibroblast", "macrophage", "assay"])
    return Artifact(
        id=f"noise_{t}_{i}", time=t, kind=ArtifactKind.MEETING_NOTE,
        text=rng.choice(_NOISE_ARTIFACTS).format(x=stem),
        reveals_edges=[], reveals_entities=[])


def generate_episode(
    seed: int,
    *,
    hop_count: int = 3,
    n_decoys: int = 1,
    noise_per_step: int = 1,
    alias_prob: float = 0.25,
    conditional_prob: float = 0.35,
    uncertainty: float = 0.1,
    invalidate: bool = False,
    time_span: int = 12,
    task_kind: Optional[TaskKind] = None,
    episode_id: Optional[str] = None,
) -> Episode:
    """Generate one fully-annotated episode."""
    rng = random.Random(seed)
    eid = episode_id or f"psmb_s{seed}_h{hop_count}"

    # -- main latent chain --------------------------------------------------
    chain_times = sorted(rng.sample(range(0, time_span - 2), hop_count))
    world = build_chain(
        rng, idx=0, hop_count=hop_count, alias_prob=alias_prob,
        conditional_prob=conditional_prob, uncertainty=uncertainty,
        invalidate=invalidate, times=chain_times)
    assert world.connection is not None
    conn = world.connection

    entities: List[Entity] = list(world.entities)
    edges: List[Edge] = list(world.edges)
    evidence: List[Evidence] = list(world.evidence)
    artifacts: List[Artifact] = list(world.artifacts)

    # near end = compound currently studied; far end = phenotype the lab tracks
    chain_ents = [e for e in world.entities
                  if e.type in (EntityType.COMPOUND, EntityType.METABOLITE,
                                EntityType.PATHWAY, EntityType.GENE,
                                EntityType.PHENOTYPE)]
    near = next(e for e in chain_ents if e.type == EntityType.COMPOUND)
    far = next(e for e in reversed(chain_ents) if e.type == EntityType.PHENOTYPE)
    near_obj_id = edges[0].object
    near_obj = world.entities and next(e for e in world.entities if e.id == near_obj_id)

    # -- old anchor: a *different* compound that also reduced the far phenotype,
    #    revealed very early. This is the fact the agent must recall unprompted.
    old_x = Entity(id="e_oldx_compound", type=EntityType.COMPOUND,
                   name=f"{rng.choice(['LEG', 'OLDX', 'ARC', 'VEN'])}-{rng.randint(10, 99)}")
    anchor_edge = Edge(
        id="anchor_edge", subject=old_x.id, predicate=Predicate.REDUCES,
        object=far.id, sign=-1, confidence=0.9, status=EdgeStatus.OBSERVED,
        evidence_id="anchor_ev", known_from=0)
    anchor_ev = Evidence(id="anchor_ev", source="anchor_art", date=0,
                         method="clonogenic assay", result=Result.POSITIVE,
                         confidence=0.9, provenance="project month 1")
    anchor_art = Artifact(
        id="anchor_art", time=0, kind=ArtifactKind.EXPERIMENT_REPORT,
        text=(f"Month 1 result (project baseline): {old_x.name} reduces "
              f"{far.name}. Filed and largely forgotten since."),
        reveals_edges=[anchor_edge.id], reveals_entities=[old_x.id, far.id],
        evidence_ids=[anchor_ev.id])
    entities.append(old_x)
    edges.append(anchor_edge)
    evidence.append(anchor_ev)
    artifacts.append(anchor_art)
    # Augment the gold subgraph: recalling old_x + the anchor edge IS the payoff.
    conn.gold_entities = list(dict.fromkeys(conn.gold_entities + [old_x.id]))
    conn.gold_edges = list(dict.fromkeys(conn.gold_edges + [anchor_edge.id]))
    conn.value = round(min(1.0, conn.value + 0.1), 2)

    connections: List[Connection] = [conn]

    # -- decoys -------------------------------------------------------------
    for d in range(n_decoys):
        dtimes = sorted(rng.sample(range(0, time_span - 2), 2))
        dworld = build_decoy(rng, idx=100 + d, hop_count=2, times=dtimes)
        assert dworld.connection is not None
        entities.extend(dworld.entities)
        edges.extend(dworld.edges)
        evidence.extend(dworld.evidence)
        artifacts.extend(dworld.artifacts)
        connections.append(dworld.connection)

    # -- noise --------------------------------------------------------------
    for t in range(0, time_span):
        for i in range(noise_per_step):
            if rng.random() < 0.6:
                artifacts.append(_noise(rng, t, i))

    # -- the probe task at the maturation point (does NOT name old facts) ----
    useful_t = conn.maturation.useful_time
    tk = task_kind or rng.choice(list(_TASK_TEMPLATES.keys()))
    prompt = _TASK_TEMPLATES[tk].format(
        near=near.name, near_obj=(near_obj.name if near_obj else far.name),
        assay=rng.choice(["RNA-seq", "flow", "viability"]))
    tasks: List[Task] = [Task(
        id="task_main", time=useful_t, kind=tk, prompt=prompt,
        target_connection_id=conn.id,
        distractor_connection_ids=[c.id for c in connections if c.is_decoy])]

    # A diagnostic explicit-recall probe (NOT the central metric) at the end.
    tasks.append(Task(
        id="task_explicit", time=time_span, kind=TaskKind.EXPLICIT_RECALL,
        prompt=(f"Diagnostic: what earlier result(s) in this project involved "
                f"{far.name}? List them."),
        target_connection_id=conn.id, explicit=True))

    artifacts.sort(key=lambda a: a.time)
    tasks.sort(key=lambda t: t.time)

    difficulty = _difficulty(conn, edges, entities, connections, time_span)
    return Episode(
        id=eid, seed=seed, entities=entities, edges=edges, evidence=evidence,
        artifacts=artifacts, connections=connections, tasks=tasks,
        difficulty=difficulty,
        notes=(f"class={conn.cls.value} hop={hop_count} decoys={n_decoys} "
               f"invalidate={invalidate}"))


def _difficulty(conn: Connection, edges: List[Edge], entities: List[Entity],
                connections: List[Connection], time_span: int) -> DifficultyProfile:
    eidx = {e.id: e for e in edges}
    gold = [eidx[x] for x in conn.gold_edges if x in eidx]
    n_cond = sum(1 for e in gold if e.condition)
    unc = [1.0 - e.confidence for e in gold]
    entset = {e.id for e in entities}
    amb = sum(1 for e in entities if e.id in conn.gold_entities and e.aliases)
    known = [e.known_from for e in gold] or [0]
    return DifficultyProfile(
        hop_count=conn.hop_count,
        semantic_distance=round(min(1.0, 0.15 * conn.hop_count), 3),
        time_distance=max(known) - min(known),
        entity_ambiguity=round(amb / max(1, len(conn.gold_entities)), 3),
        conditionality=round(n_cond / max(1, len(gold)), 3),
        distractor_density=round(
            sum(1 for c in connections if c.is_decoy) / max(1, len(connections)), 3),
        evidence_uncertainty=round(sum(unc) / max(1, len(unc)), 3),
    )


# --------------------------------------------------------------------------- #
# Signature episode -- hand-authored to mirror the canonical example:
#   Z -> M -> A -> Y  (and Y ~ P, and old Drug X -> P)
# Kept legible on purpose so a reader can trace the intended payoff.
# --------------------------------------------------------------------------- #
def signature_episode() -> Episode:
    E = Entity
    z = E("z", EntityType.COMPOUND, "Drug Z")
    x = E("x", EntityType.COMPOUND, "Drug X")
    m = E("m", EntityType.METABOLITE, "metabolite M (itaconate)", ["itaconate"])
    a = E("a", EntityType.PATHWAY, "pathway A (NRF2)", ["NRF2"])
    y = E("y", EntityType.PATHWAY, "pathway Y (ferroptosis)", ["ferroptosis axis"])
    p = E("p", EntityType.PHENOTYPE, "phenotype P (clonogenic survival)",
          ["clonogenic survival"])
    cond = E("cond", EntityType.CONDITION, "inflammatory conditions")
    entities = [z, x, m, a, y, p, cond]

    # edges with realistic reveal times (months)
    e_xp = Edge("e_xp", x.id, Predicate.REDUCES, p.id, -1, known_from=1,
                confidence=0.9, evidence_id="v_xp")
    e_yp = Edge("e_yp", y.id, Predicate.REDUCES, p.id, -1, known_from=2,
                confidence=0.85, evidence_id="v_yp")  # CRISPR loss of Y reduces P
    e_zm = Edge("e_zm", z.id, Predicate.ALTERS, m.id, 1, known_from=4,
                confidence=0.8, evidence_id="v_zm")
    e_ma = Edge("e_ma", m.id, Predicate.LINKS, a.id, 1, known_from=7,
                confidence=0.75, evidence_id="v_ma")
    e_ay = Edge("e_ay", a.id, Predicate.REGULATES, y.id, 1, condition=cond.id,
                known_from=10, confidence=0.7, evidence_id="v_ay")
    edges = [e_xp, e_yp, e_zm, e_ma, e_ay]

    def ev(vid, src, date, method, conf):
        return Evidence(id=vid, source=src, date=date, method=method,
                        result=Result.POSITIVE, confidence=conf)

    evidence = [
        ev("v_xp", "art_xp", 1, "clonogenic assay", 0.9),
        ev("v_yp", "art_yp", 2, "CRISPR loss-of-function", 0.85),
        ev("v_zm", "art_zm", 4, "mass spec", 0.8),
        ev("v_ma", "art_ma", 7, "literature", 0.75),
        ev("v_ay", "art_ay", 10, "RNA-seq + epistasis", 0.7),
    ]

    A = Artifact
    artifacts = [
        A("art_xp", 1, ArtifactKind.EXPERIMENT_REPORT,
          "Month 1: Drug X reduces phenotype P (clonogenic survival). Baseline result.",
          ["e_xp"], ["x", "p"], ["v_xp"]),
        A("art_yp", 2, ArtifactKind.EXPERIMENT_REPORT,
          "Month 2: CRISPR loss of pathway Y (ferroptosis) also reduces phenotype P.",
          ["e_yp"], ["y", "p"], ["v_yp"]),
        A("art_zm", 4, ArtifactKind.ASSAY_OBSERVATION,
          "Month 4: Drug Z unexpectedly alters metabolite M (itaconate) levels.",
          ["e_zm"], ["z", "m"], ["v_zm"]),
        A("art_ma", 7, ArtifactKind.PAPER_SUMMARY,
          "Month 7: New paper links metabolite M (itaconate) to pathway A (NRF2).",
          ["e_ma"], ["m", "a"], ["v_ma"]),
        A("art_ay", 10, ArtifactKind.EXPERIMENT_REPORT,
          "Month 10: New experiment shows pathway A (NRF2) regulates pathway Y "
          "(ferroptosis) under inflammatory conditions.",
          ["e_ay"], ["a", "y", "cond"], ["v_ay"]),
        A("noise_3", 3, ArtifactKind.MEETING_NOTE,
          "Month 3: freezer inventory reconciled; nothing notable.", [], []),
        A("noise_8", 8, ArtifactKind.MEETING_NOTE,
          "Month 8: instrument QC passed; grant report submitted.", [], []),
    ]

    conn = Connection(
        id="conn_zx", cls=ConnectionClass.SERENDIPITOUS_REPURPOSING,
        path_edges=["e_zm", "e_ma", "e_ay", "e_yp"],
        gold_entities=["z", "m", "a", "y", "p", "x"],
        gold_edges=["e_zm", "e_ma", "e_ay", "e_yp", "e_xp"],
        maturation=Maturation(birth_time=1, solvable_time=10, useful_time=11,
                              best_window=(11, 13), expiry_time=None),
        value=1.0,
        rationale=("Designing a study around Drug Z should reactivate the old "
                   "Drug X -> P result via the M -> A -> Y axis: Z alters M, M "
                   "links A, A regulates Y (inflammatory), Y loss reduces P, and "
                   "X reduced P. Same phenotype, converging mechanism."))

    tasks = [
        Task(id="task_main", time=11, kind=TaskKind.DESIGN_EXPERIMENT,
             prompt=("I'm designing the next study around Drug Z. Its effect on "
                     "metabolite M looked interesting. What experiment should we "
                     "run next, and is there anything from earlier in this project "
                     "we ought to connect it to?"),
             target_connection_id="conn_zx", distractor_connection_ids=[]),
        Task(id="task_explicit", time=12, kind=TaskKind.EXPLICIT_RECALL,
             prompt=("Diagnostic: which earlier results in this project involved "
                     "phenotype P (clonogenic survival)? List them."),
             target_connection_id="conn_zx", explicit=True),
    ]
    difficulty = DifficultyProfile(
        hop_count=4, semantic_distance=0.7, time_distance=9,
        entity_ambiguity=0.5, conditionality=0.2, distractor_density=0.0,
        evidence_uncertainty=0.21)
    return Episode(
        id="psmb_signature", seed=0, entities=entities, edges=edges,
        evidence=evidence, artifacts=sorted(artifacts, key=lambda a: a.time),
        connections=[conn], tasks=tasks, difficulty=difficulty,
        notes="Hand-authored canonical example (Z->M->A->Y, Y~P, X->P).")


def generate_dataset(n: int, *, seed0: int = 1000, out_dir: Optional[Path] = None,
                     include_signature: bool = True, **kw) -> List[Episode]:
    episodes: List[Episode] = []
    if include_signature:
        episodes.append(signature_episode())
    for i in range(n):
        seed = seed0 + i
        # vary strata deterministically
        hop = 2 + (i % 4)
        episodes.append(generate_episode(
            seed, hop_count=hop, n_decoys=(i % 3),
            invalidate=(i % 5 == 4), **kw))
    if out_dir is not None:
        write_dataset(episodes, out_dir)
    return episodes


def write_dataset(episodes: List[Episode], out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for ep in episodes:
        path = out_dir / f"{ep.id}.json"
        path.write_text(ep.to_json(), encoding="utf-8")
        index.append({"id": ep.id, "seed": ep.seed,
                      "difficulty": ep.difficulty.score(),
                      "hop_count": ep.difficulty.hop_count,
                      "class": ep.connections[0].cls.value,
                      "file": path.name})
    (out_dir / "index.json").write_text(
        json.dumps({"schema_version": episodes[0].schema_version if episodes else "0",
                    "episodes": index}, indent=2), encoding="utf-8")
    return out_dir


def load_dataset(in_dir: Path) -> List[Episode]:
    in_dir = Path(in_dir)
    idx = json.loads((in_dir / "index.json").read_text(encoding="utf-8"))
    return [Episode.from_json((in_dir / e["file"]).read_text(encoding="utf-8"))
            for e in idx["episodes"]]


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a PSMB dataset.")
    ap.add_argument("--n", type=int, default=8, help="number of random episodes")
    ap.add_argument("--seed0", type=int, default=1000)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).parent / "data" / "dataset")
    ap.add_argument("--no-signature", action="store_true")
    args = ap.parse_args(argv)
    eps = generate_dataset(args.n, seed0=args.seed0, out_dir=args.out,
                           include_signature=not args.no_signature)
    print(f"Wrote {len(eps)} episodes to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
