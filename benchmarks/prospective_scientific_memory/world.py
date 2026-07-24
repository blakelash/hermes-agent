"""Tier-1 synthetic causal-world generation for PSMB.

Builds seeded latent chains through a scientific world graph, renders each
fragment as a natural-language artifact revealed at a specific point in time,
and attaches a maturation lifecycle. Fully deterministic given a seed.

The generator deliberately supports controlled strata (hop depth, conditional
edges, entity ambiguity via aliases, decoy density, evidence uncertainty) so
difficulty can be varied independently of raw hop count.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from benchmarks.prospective_scientific_memory.schema import (
    Artifact,
    ArtifactKind,
    Connection,
    ConnectionClass,
    Edge,
    EdgeStatus,
    Entity,
    EntityType,
    Evidence,
    Maturation,
    Predicate,
    Result,
)

# --------------------------------------------------------------------------- #
# Name banks -- synthetic but plausible. Numeric suffixes are drawn from the RNG
# so distinct episodes do not collide lexically (defeats trivial string match).
# --------------------------------------------------------------------------- #
_COMPOUND_PREFIX = ["DRX", "CPD", "NVX", "AZ", "BMS", "GSK", "LY", "MK", "PF", "RG"]
_METABOLITE_STEM = ["itaconate", "kynurenine", "succinate", "spermidine",
                    "lactoyl-CoA", "hydroxyglutarate", "acetyl-lysine", "urate"]
_PATHWAY_STEM = ["NRF2", "mTORC1", "NF-kB", "STING", "Wnt", "Hippo", "JAK-STAT",
                "AMPK", "TGF-beta", "cGAS", "ferroptosis", "OXPHOS"]
_GENE_STEM = ["GPX4", "SLC7A11", "ATF4", "TFEB", "IRF3", "HMOX1", "CDKN1A",
             "SIRT3", "ACOD1", "IDO1", "KEAP1", "MYC"]
_PHENO_STEM = ["clonogenic survival", "IL-2 secretion", "mitochondrial ROS",
              "T-cell exhaustion", "epithelial migration", "lipid peroxidation",
              "senescence", "phagocytic capacity"]
_CELLTYPE_STEM = ["CD8 T cells", "M1 macrophages", "cancer-associated fibroblasts",
                 "melanoma cells", "regulatory T cells", "dendritic cells"]
_CONDITION_STEM = ["inflammatory conditions", "hypoxia", "glucose starvation",
                  "high dose (>1 uM)", "48h exposure", "IFN-gamma priming"]
_ASSAY_STEM = ["RNA-seq", "flow cytometry", "Seahorse", "CRISPRi screen",
              "mass spec", "live-cell imaging", "Western blot"]


def _entity_bank(rng: random.Random) -> Dict[EntityType, List[str]]:
    """Curated stems per type, shuffled for this episode."""
    bank: Dict[EntityType, List[str]] = {
        EntityType.METABOLITE: list(_METABOLITE_STEM),
        EntityType.PATHWAY: list(_PATHWAY_STEM),
        EntityType.GENE: list(_GENE_STEM),
        EntityType.PHENOTYPE: list(_PHENO_STEM),
        EntityType.CELL_TYPE: list(_CELLTYPE_STEM),
        EntityType.CONDITION: list(_CONDITION_STEM),
        EntityType.ASSAY: list(_ASSAY_STEM),
    }
    for v in bank.values():
        rng.shuffle(v)
    return bank


class _Namer:
    """Hands out unique entity ids/names for one episode."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.bank = _entity_bank(rng)
        self._used: Dict[EntityType, int] = {}
        self._n = 0

    def _next(self, t: EntityType) -> str:
        i = self._used.get(t, 0)
        self._used[t] = i + 1
        pool = self.bank.get(t, [])
        if t == EntityType.COMPOUND:
            pre = self.rng.choice(_COMPOUND_PREFIX)
            return f"{pre}-{self.rng.randint(100, 999)}"
        if i < len(pool):
            return pool[i]
        return f"{t.value}-{self.rng.randint(1000, 9999)}"

    def make(self, t: EntityType, alias_prob: float = 0.0) -> Entity:
        self._n += 1
        name = self._next(t)
        eid = f"e{self._n}_{t.value}"
        aliases: List[str] = []
        if self.rng.random() < alias_prob:
            aliases.append(_synonym(name, self.rng))
        return Entity(id=eid, type=t, name=name, aliases=aliases)


def _synonym(name: str, rng: random.Random) -> str:
    """Fabricate a plausible alias to force synonym/orthology resolution."""
    tags = ["(clone A2)", "ortholog", "prev. named", "aka"]
    suffix = rng.choice(["-b", " isoform 2", " (murine)", "*", " v2"])
    if rng.random() < 0.5:
        return f"{name}{suffix}"
    return f"{rng.choice(tags)} {name}"


# --------------------------------------------------------------------------- #
# Chain templates: each hop is (predicate, sign). The entity-type sequence is
# chosen to read like a real mechanistic axis.
# --------------------------------------------------------------------------- #
_TYPE_CYCLE = [
    EntityType.COMPOUND,
    EntityType.METABOLITE,
    EntityType.PATHWAY,
    EntityType.GENE,
    EntityType.PHENOTYPE,
]

_PRED_BY_STEP = {
    (EntityType.COMPOUND, EntityType.METABOLITE): (Predicate.ALTERS, 1),
    (EntityType.METABOLITE, EntityType.PATHWAY): (Predicate.LINKS, 1),
    (EntityType.PATHWAY, EntityType.GENE): (Predicate.REGULATES, 1),
    (EntityType.PATHWAY, EntityType.PATHWAY): (Predicate.UPSTREAM_OF, 1),
    (EntityType.GENE, EntityType.PHENOTYPE): (Predicate.REDUCES, -1),
    (EntityType.PATHWAY, EntityType.PHENOTYPE): (Predicate.REDUCES, -1),
    (EntityType.COMPOUND, EntityType.PHENOTYPE): (Predicate.REDUCES, -1),
}

_ARTIFACT_TEMPLATES = {
    Predicate.ALTERS: [
        "{assay} on {cond} showed {subj} unexpectedly {dir} {obj} levels.",
        "Follow-up metabolomics: {subj} treatment shifts {obj} (effect {dir}).",
    ],
    Predicate.LINKS: [
        "New paper ({paper}) links {subj} to {obj} signaling.",
        "Lit search: {subj} is reported to feed into the {obj} axis.",
    ],
    Predicate.REGULATES: [
        "{assay} indicates {subj} regulates {obj}, but only under {cond}.",
        "Screen hit: {subj} modulates {obj} expression ({cond}).",
    ],
    Predicate.UPSTREAM_OF: [
        "Epistasis places {subj} upstream of {obj} ({cond}).",
        "{assay}: perturbing {subj} moves {obj}; {subj} appears upstream.",
    ],
    Predicate.REDUCES: [
        "{assay}: {subj} loss/inhibition reduces {obj}.",
        "CRISPR loss of {subj} phenocopies a drop in {obj}.",
    ],
}

_DIR_WORD = {1: "raises", -1: "reduces", 0: "shifts"}


def _render_edge(edge: Edge, ents: Dict[str, Entity], rng: random.Random,
                 paper: Optional[Entity] = None, assay: Optional[Entity] = None) -> str:
    subj = ents[edge.subject].name
    obj = ents[edge.object].name
    cond = ents[edge.condition].name if edge.condition else "standard conditions"
    tmpl = rng.choice(_ARTIFACT_TEMPLATES.get(edge.predicate, ["{subj} -> {obj}."]))
    return tmpl.format(
        subj=subj, obj=obj, cond=cond, dir=_DIR_WORD.get(edge.sign, "shifts"),
        paper=(paper.name if paper else "preprint 2026"),
        assay=(assay.name if assay else "assay"),
    )


# --------------------------------------------------------------------------- #
# Core: build one latent mechanistic chain + its trajectory fragments.
# --------------------------------------------------------------------------- #
class ChainWorld:
    """Container for a generated chain and everything needed to place it in an
    episode. Times are relative offsets; the episode composer shifts them."""

    def __init__(self) -> None:
        self.entities: List[Entity] = []
        self.edges: List[Edge] = []
        self.evidence: List[Evidence] = []
        self.artifacts: List[Artifact] = []
        self.connection: Optional[Connection] = None


def build_chain(
    rng: random.Random,
    *,
    idx: int,
    hop_count: int = 3,
    alias_prob: float = 0.0,
    conditional_prob: float = 0.3,
    uncertainty: float = 0.0,
    invalidate: bool = False,
    times: Optional[List[int]] = None,
    cls: ConnectionClass = ConnectionClass.MECHANISTIC_CHAIN,
) -> ChainWorld:
    """Build a linear latent chain of ``hop_count`` edges.

    The chain reads far-end (old anchor, e.g. a phenotype the lab cares about)
    to near-end (a compound the scientist is *currently* studying). Evidence is
    revealed across ``times`` (one timestamp per edge, chronological but not in
    chain order -- fragments arrive scrambled, which is the whole point).
    """
    namer = _Namer(rng)
    n_ent = hop_count + 1
    types = [_TYPE_CYCLE[min(i, len(_TYPE_CYCLE) - 1)] for i in range(n_ent)]
    # Force a phenotype at the far end and a compound at the near end.
    types[-1] = EntityType.PHENOTYPE
    types[0] = EntityType.COMPOUND
    ents = [namer.make(t, alias_prob=alias_prob) for t in types]

    # A shared condition entity used to gate some edges.
    cond_ent = namer.make(EntityType.CONDITION)
    assay_ent = namer.make(EntityType.ASSAY)
    paper_ent = Entity(id=f"e_paper_{idx}", type=EntityType.PAPER,
                       name=f"Ref-{rng.randint(2020, 2026)}-{rng.randint(100, 999)}")

    if times is None:
        times = sorted(rng.sample(range(0, 12), hop_count))
    assert len(times) == hop_count, "need one timestamp per edge"

    ent_index = {e.id: e for e in ents}
    ent_index[cond_ent.id] = cond_ent

    edges: List[Edge] = []
    evidence: List[Evidence] = []
    artifacts: List[Artifact] = []

    # Build edges chain[i]: ents[i] -> ents[i+1]
    for i in range(hop_count):
        s, o = ents[i], ents[i + 1]
        pred, sign = _PRED_BY_STEP.get(
            (s.type, o.type), (Predicate.REGULATES, 1))
        gated = rng.random() < conditional_prob
        conf = 1.0
        if uncertainty > 0:
            conf = round(max(0.35, 1.0 - abs(rng.gauss(0, uncertainty))), 2)
        t = times[i]
        eid = f"c{idx}_edge{i}"
        vid = f"c{idx}_ev{i}"
        edge = Edge(
            id=eid, subject=s.id, predicate=pred, object=o.id, sign=sign,
            condition=cond_ent.id if gated else None, confidence=conf,
            status=EdgeStatus.OBSERVED, evidence_id=vid, known_from=t,
        )
        edges.append(edge)
        method = assay_ent.name
        evidence.append(Evidence(
            id=vid, source=f"c{idx}_art{i}", date=t, method=method,
            result=Result.POSITIVE, confidence=conf,
            provenance=f"internal-{rng.randint(10, 99)}"))
        # pick artifact kind by edge role
        kind = _artifact_kind_for(pred, rng)
        text = _render_edge(edge, ent_index, rng,
                            paper=paper_ent if pred == Predicate.LINKS else None,
                            assay=assay_ent)
        artifacts.append(Artifact(
            id=f"c{idx}_art{i}", time=t, kind=kind, text=text,
            reveals_edges=[eid], reveals_entities=[s.id, o.id],
            evidence_ids=[vid]))

    if invalidate and hop_count >= 2:
        # Invalidate a middle edge late in the timeline: turns a live chain stale.
        mid = edges[hop_count // 2]
        inv_t = max(times) + 2
        mid.invalidated_at = inv_t
        mid.status = EdgeStatus.CONTRADICTED
        artifacts.append(Artifact(
            id=f"c{idx}_inv", time=inv_t, kind=ArtifactKind.PAPER_SUMMARY,
            text=(f"Retraction/failure to replicate: the "
                  f"{ent_index[mid.subject].name} -> {ent_index[mid.object].name} "
                  f"link did not hold on repeat; treat as contradicted."),
            reveals_edges=[mid.id], reveals_entities=[mid.subject, mid.object]))

    gold_edges = [e.id for e in edges]
    gold_entities = [e.id for e in ents]  # exclude condition/assay from minimal G*
    birth = min(times)
    solvable = max(times)
    expiry = (max(times) + 2) if invalidate else None

    world = ChainWorld()
    world.entities = ents + [cond_ent, assay_ent, paper_ent]
    world.edges = edges
    world.evidence = evidence
    world.artifacts = artifacts
    world.connection = Connection(
        id=f"conn{idx}", cls=cls, path_edges=gold_edges,
        gold_entities=gold_entities, gold_edges=gold_edges,
        maturation=Maturation(
            birth_time=birth, solvable_time=solvable, useful_time=solvable + 1,
            best_window=(solvable + 1, solvable + 3), expiry_time=expiry),
        value=round(rng.uniform(0.6, 1.0), 2),
        rationale=_chain_rationale(ents, edges, ent_index),
    )
    return world


def _artifact_kind_for(pred: Predicate, rng: random.Random) -> ArtifactKind:
    if pred == Predicate.LINKS:
        return rng.choice([ArtifactKind.PAPER_SUMMARY, ArtifactKind.DB_SEARCH])
    if pred == Predicate.ALTERS:
        return rng.choice([ArtifactKind.ASSAY_OBSERVATION,
                          ArtifactKind.EXPERIMENT_REPORT])
    return rng.choice([ArtifactKind.EXPERIMENT_REPORT, ArtifactKind.MEETING_NOTE,
                      ArtifactKind.CODE_OUTPUT])


def _chain_rationale(ents: List[Entity], edges: List[Edge],
                     idx: Dict[str, Entity]) -> str:
    names = " -> ".join(e.name for e in ents)
    return (f"Latent chain {names}: connects the currently-studied "
            f"{ents[0].name} back to the {ents[-1].name} the lab tracks.")


def build_decoy(rng: random.Random, *, idx: int, hop_count: int = 2,
                times: Optional[List[int]] = None) -> ChainWorld:
    """A plausible-but-useless chain (a distractor). Marked is_decoy=True and
    given a broken link so surfacing it should count against precision."""
    world = build_chain(rng, idx=idx, hop_count=hop_count, times=times,
                        conditional_prob=0.2)
    assert world.connection is not None
    world.connection.is_decoy = True
    world.connection.cls = ConnectionClass.ANALOGICAL
    world.connection.rationale = "Decoy: superficially similar, not mechanistically valid."
    # Break a link so the chain is not actually inferable.
    if world.edges:
        world.edges[-1].status = EdgeStatus.PROPOSED
        world.edges[-1].confidence = 0.25
    return world
