"""Tests for the Prospective Scientific Memory Benchmark (PSMB) prototype.

Deterministic, offline (no network, no LLM), stdlib + pytest only. These
exercise the real generation and scoring code paths against the shipped mock
"brain" via the CallableAgentClient -- validating the harness end-to-end without
the heavy agent stack.
"""

from __future__ import annotations

import pytest

from benchmarks.prospective_scientific_memory.generate import (
    generate_dataset,
    generate_episode,
    signature_episode,
)
from benchmarks.prospective_scientific_memory.memory_modes import (
    IMPLEMENTED_MODES,
    ModeSpec,
    clip_history,
    is_implemented,
)
from benchmarks.prospective_scientific_memory.mock_server import (
    _extract_entities,
    respond_policy,
)
from benchmarks.prospective_scientific_memory.runner import (
    CallableAgentClient,
    run_episode,
)
from benchmarks.prospective_scientific_memory.schema import Entity, EntityType, Episode
from benchmarks.prospective_scientific_memory.scoring import (
    RunLog,
    mentioned,
    name_variants,
    pairwise_memory_value,
    score_connection_recovery,
    score_episode,
)


def _client(policy: str) -> CallableAgentClient:
    return CallableAgentClient(lambda msgs: respond_policy(msgs, policy=policy))


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def test_generation_deterministic_and_roundtrips():
    a = generate_episode(42, hop_count=3, n_decoys=2, invalidate=True)
    b = generate_episode(42, hop_count=3, n_decoys=2, invalidate=True)
    assert a.to_json() == b.to_json()
    # a different seed differs
    c = generate_episode(43, hop_count=3, n_decoys=2, invalidate=True)
    assert c.to_json() != a.to_json()
    # JSON round-trip is lossless for the fields we score on
    r = Episode.from_json(a.to_json())
    assert [e.id for e in r.edges] == [e.id for e in a.edges]
    assert r.connections[0].gold_edges == a.connections[0].gold_edges
    assert r.connections[0].maturation.best_window == a.connections[0].maturation.best_window


def test_signature_episode_structure():
    ep = signature_episode()
    conn = ep.connections[0]
    # the old anchor (Drug X) and the far phenotype P must be in the gold graph
    assert "x" in conn.gold_entities and "p" in conn.gold_entities
    # the main task must not name the old fact (no "Drug X" in the prompt)
    main = next(t for t in ep.tasks if t.id == "task_main")
    assert "Drug X" not in main.prompt
    assert main.target_connection_id == conn.id
    # maturation ordering is sane
    m = conn.maturation
    assert m.birth_time <= m.solvable_time <= m.useful_time


def test_dataset_index_and_load(tmp_path):
    eps = generate_dataset(3, seed0=100, out_dir=tmp_path, include_signature=True)
    assert len(eps) == 4  # signature + 3
    from benchmarks.prospective_scientific_memory.generate import load_dataset
    loaded = load_dataset(tmp_path)
    assert {e.id for e in loaded} == {e.id for e in eps}


# --------------------------------------------------------------------------- #
# Mention detection
# --------------------------------------------------------------------------- #
def test_name_variants_splits_parenthetical():
    e = Entity("p", EntityType.PHENOTYPE, "phenotype P (clonogenic survival)",
               ["clonogenic survival"])
    vs = set(name_variants(e))
    assert "clonogenic survival" in vs
    # single-letter "phenotype P" head is short-filtered but full name kept
    assert any("clonogenic" in v for v in vs)


def test_mentioned_word_boundary():
    e = Entity("g", EntityType.GENE, "NRF2")
    assert mentioned("we see NRF2 activation", e)
    assert not mentioned("NRF20 is different", e)


def test_extract_entities_recognizes_vocab_and_codes():
    txt = "Drug Z alters itaconate; DRX-114 hit NRF2 in CD8 T cells."
    found = {f.lower() for f in _extract_entities(txt)}
    assert "itaconate" in found
    assert "drx-114" in found
    assert any("nrf2" == f for f in found)


# --------------------------------------------------------------------------- #
# Scoring behavior across memory modes and policies
# --------------------------------------------------------------------------- #
def test_full_context_beats_no_memory_signature():
    ep = signature_episode()
    with_run = run_episode(ep, _client("recall"), mode="full_context")
    without_run = run_episode(ep, _client("recall"), mode="no_memory")
    s_with = score_episode(ep, with_run)
    s_without = score_episode(ep, without_run)
    assert s_with.trigger.opportunity_recall == 1.0
    assert s_with.trigger.mean_timeliness == 1.0
    assert s_without.trigger.opportunity_recall == 0.0
    assert s_with.task_utility > s_without.task_utility
    mv = pairwise_memory_value(
        {"full_context": [s_with], "no_memory": [s_without]},
        with_mode="full_context", without_mode="no_memory")
    assert mv["mean_memory_value"] > 0.5


def test_myopic_policy_scores_zero():
    ep = signature_episode()
    run = run_episode(ep, _client("myopic"), mode="full_context")
    s = score_episode(ep, run)
    assert s.trigger.opportunity_recall == 0.0
    assert s.task_utility == 0.0


def test_noisy_policy_has_recall_but_intolerable_pmu():
    # Spray-everything agent: excellent recall, wrecked PMU + interruption.
    ep = signature_episode()
    run = run_episode(ep, _client("noisy"), mode="full_context")
    s = score_episode(ep, run)
    assert s.trigger.opportunity_recall == 1.0
    assert s.trigger.interruption_burden > 0
    assert s.trigger.pmu < 0


def test_dataset_level_memory_value_positive():
    eps = generate_dataset(4, seed0=7000, include_signature=True)
    by_mode = {"no_memory": [], "full_context": []}
    for ep in eps:
        for mode in by_mode:
            by_mode[mode].append(score_episode(ep, run_episode(ep, _client("recall"), mode=mode)))
    mv = pairwise_memory_value(by_mode, with_mode="full_context", without_mode="no_memory")
    assert mv["n"] == len(eps)
    assert mv["mean_memory_value"] > 0.3
    assert mv["mean_utility_without"] == 0.0


# --------------------------------------------------------------------------- #
# Timeliness / maturation semantics
# --------------------------------------------------------------------------- #
def test_premature_surfacing_gets_no_opportunity_credit():
    # Build a run where the connection is surfaced ONLY before solvable_time.
    ep = signature_episode()
    conn = ep.connections[0]
    from benchmarks.prospective_scientific_memory.scoring import TurnRecord
    # craft a task turn placed before solvable_time with a full recall response
    early = conn.maturation.solvable_time - 1
    resp = ("Connecting to Drug X, phenotype P, pathway Y, pathway A, "
            "metabolite M, Drug Z now.")
    run = RunLog(episode_id=ep.id, memory_mode="full_context", turns=[
        TurnRecord(time=early, kind="task", stimulus_text="do something",
                   response_text=resp, task_id="task_main",
                   target_connection_id=conn.id)])
    # temporarily move the task earlier to simulate premature probing
    for t in ep.tasks:
        if t.id == "task_main":
            t.time = early
    s = score_episode(ep, run)
    # surfaced early (before solvable) => not counted as opportunity, penalized
    assert s.trigger.opportunity_recall == 0.0
    assert s.trigger.false_injections >= 1


def test_connection_recovery_spontaneity_excludes_stimulus():
    ep = signature_episode()
    conn = ep.connections[0]
    # If the stimulus already names Drug X, recalling it isn't spontaneous.
    resp = "This connects to Drug X and phenotype P."
    rec_no_stim = score_connection_recovery(ep, conn, resp, stimulus="")
    rec_with_stim = score_connection_recovery(ep, conn, resp,
                                              stimulus="What about Drug X?")
    assert rec_with_stim.spontaneous_recall <= rec_no_stim.spontaneous_recall


# --------------------------------------------------------------------------- #
# Memory modes
# --------------------------------------------------------------------------- #
def test_clip_history_modes():
    hist = [{"role": "user", "content": str(i)} for i in range(10)]
    assert clip_history(hist, ModeSpec("no_memory")) is None
    assert clip_history(hist, ModeSpec("full_context")) == hist
    win = clip_history(hist, ModeSpec("sliding_window", window=2))
    assert win == hist[-4:]


def test_planned_modes_raise():
    assert set(IMPLEMENTED_MODES) == {"no_memory", "full_context"}
    assert is_implemented("full_context") and not is_implemented("vector_rag")
    with pytest.raises(NotImplementedError):
        clip_history([], ModeSpec("vector_rag"))
