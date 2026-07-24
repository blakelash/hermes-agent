# Prospective Scientific Memory Benchmark (PSMB)

An initial prototype of a benchmark for **prospective associative memory for
discovery**: while doing ordinary new scientific work, does an agent
*spontaneously* recognize that several previously disconnected observations now
form a useful hypothesis — **without anyone asking it to search the past**?

This is deliberately *not* "given an explicit memory question, retrieve the old
fact." That is a librarian test. The target capability is a collaborator that
notices when an old memory becomes relevant even though neither the user nor the
current task asks it to recall anything.

> Canonical example (shipped as the `psmb_signature` episode):
> Month 1 Drug X reduces phenotype P · Month 2 CRISPR loss of pathway Y also
> reduces P · Month 4 Drug Z alters metabolite M · Month 7 a paper links M to
> pathway A · Month 10 A regulates Y under inflammatory conditions. Now the
> scientist is *designing a study around Drug Z*. Nothing says "recall X." A
> prospective-memory agent should surface: *there may be a mechanistic link
> between your Drug Z phenotype and the old Drug X result via the M–A–Y axis.*

## Why this is hard (the five components)

1. **Writing** — did the system preserve the scientifically important observations?
2. **Representation** — entities, conditions, uncertainty, provenance, relationships.
3. **Association** — traverse several weak/heterogeneous links.
4. **Triggering** — know *when* the connection has become relevant.
5. **Judgment** — is the connection *useful*, not merely possible?

Most existing memory benchmarks test only pieces of (1) and (3) with explicit
queries. PSMB centers (4) and (5), which are usually omitted.

## What the prototype implements (this directory)

| Piece | File | Status |
|---|---|---|
| Ground-truth data model (entities, typed/signed/conditional edges, evidence, maturation) | `schema.py` | ✅ |
| Tier-1 synthetic causal-world generator (seeded, controlled strata) | `world.py` | ✅ |
| Episode composer + hand-authored signature episode + dataset IO | `generate.py` | ✅ |
| Runner that drives the **real Hermes agent** turn-by-turn | `runner.py` | ✅ |
| Memory-mode baselines: `no_memory`, `full_context` (+ extension points) | `memory_modes.py` | ✅ (2 of 10) |
| Layer A–D scorers + PMU + Memory-Value regret | `scoring.py` | ✅ |
| Offline mock OpenAI endpoint (harness validation, no credentials) | `mock_server.py` | ✅ |
| Report aggregation | `report.py` | ✅ |
| CLI (`generate` / `run` / `score` / `demo`) | `__main__.py` | ✅ |
| Tests | `../../tests/benchmarks/test_psmb.py` | ✅ |

The prototype is **self-contained** and touches **no Hermes core files** (it is
research tooling per the Footprint Ladder, not a core tool). Generation and
scoring have zero dependency on the agent stack; only the runner lazy-imports
`AIAgent`.

## Quickstart

```bash
source .venv/bin/activate

# 1. Self-contained end-to-end demo: spins up an offline mock model, drives the
#    REAL Hermes agent through episodes in no_memory vs full_context, and scores.
python -m benchmarks.prospective_scientific_memory demo --n 2

# 2. Generate a dataset of episodes to disk (inspectable JSON + index.json).
python -m benchmarks.prospective_scientific_memory generate --n 8 --out /tmp/psmb

# 3. Run against a real OpenAI-compatible endpoint (a real model under test).
python -m benchmarks.prospective_scientific_memory run \
  --dataset /tmp/psmb --modes no_memory,full_context \
  --base-url https://your-endpoint/v1 --model your-model --api-key "$KEY"

# 4. Score a single (episode, runlog) pair offline.
python -m benchmarks.prospective_scientific_memory score \
  --episode /tmp/psmb/psmb_signature.json --runlog /tmp/psmb_runs/runlog_psmb_signature_full_context.json
```

A committed, inspectable reference episode lives at
`signature_episode.example.json` (the canonical Z–M–A–Y / X–P example). Full
datasets are written under `data/` (gitignored scratch) and are deterministically
regenerable from a seed.

## The benchmark design

### Unit of evaluation
The primary unit is **not** a Q/A pair. It is a *longitudinal scientific
trajectory* containing latent connection opportunities. Fragments of a hidden
world graph are revealed over "months" as experiment reports, meeting notes,
paper summaries, failed analyses, assay observations, etc. The agent never sees
the whole graph.

### Connection maturation (`schema.Maturation`)
Every latent connection carries a lifecycle so we can distinguish **memory from
clairvoyance**:
- `birth_time` — first relevant evidence appeared
- `solvable_time` — enough evidence existed to infer the chain
- `useful_time` — became relevant to an actual decision
- `best_window` — when surfacing helps most
- `expiry_time` — contrary evidence invalidated it

An agent gets **no credit** for blurting a connection before it is inferable, and
is **penalized** for surfacing an invalidated one.

### No explicit memory questions (the central metric)
The main probe places the agent in an ordinary task — *design the next
experiment*, *interpret this result*, *prioritize compounds*, *propose a
mechanism* — that never names the old facts. A diagnostic `EXPLICIT_RECALL` probe
exists but is **not** the central score (it is a librarian subtest).

### Connection classes (`ConnectionClass`)
Mechanistic chains, analogical, convergent evidence, contradiction resolution,
negative-evidence, methodological transfer, and serendipitous repurposing (the
hardest for lexical retrieval — current and historical episodes share little
surface text).

### Difficulty is more than hop count (`DifficultyProfile`)
```
difficulty = f(hop_count, semantic_distance, time_distance, entity_ambiguity,
               conditionality, distractor_density, evidence_uncertainty)
```
The generator varies these independently (aliases force synonym/orthology
resolution; conditional edges gate on dose/timing/species; decoys add distractor
density; per-edge confidence adds uncertainty).

## What is scored (four layers)

Detection is **deterministic and structural** — entity/edge mention over the
ground-truth minimal subgraph `G*`, retaining the full evidence chain rather than
grading only a concluding sentence. It does **not** ask another LLM whether the
prose "sounds right," so scores are hard to game and run fully offline. The
detectors are pluggable (`scoring.EntityDetector`) so a stricter NLI/LLM-judge can
be swapped in without changing the metric definitions.

Spontaneity is enforced: recall is only credited over gold entities **not present
in the turn's stimulus** — naming what the prompt just said is not recall.

- **A. Memory integrity** (`score_memory_integrity`) — evidence recall,
  condition fidelity, invalidated-memory suppression (scored on the diagnostic
  probe).
- **B. Connection recovery** (`score_connection_recovery`) — entity/edge recall
  and precision against `G*` (edge = both endpoints named; precision proxied
  against decoy edges).
- **C. Trigger quality** (`score_trigger_quality`) — **the piece most benchmarks
  omit**: opportunity recall, intervention precision, timeliness, false
  injections, interruption burden, redundancy, latency, and
  **Prospective Memory Utility**
  `PMU = Σ_i V_i · R_i · T_i − λ·F_i − γ·I_i`.
- **D. Downstream utility** (`memory_value`) — the honest test:
  `Memory Value = utility(with memory) − utility(without memory)`. If the same
  decision is reached either way, memory did not contribute.

### The tradeoff the metrics expose
Running the shipped mock at three policies reproduces the core thesis:

| policy | opportunity_recall | PMU | interruption |
|---|---|---|---|
| `myopic` (never recalls) | 0.0 | 0.0 | 0 |
| `recall` (selective, timely) | 1.0 | ≈0 (best) | 0 |
| `noisy` (sprays every turn) | 1.0 | **−9.75** | 13.1 |

The spray-everything agent has *excellent recall and is completely intolerable* —
which is exactly why a single accuracy number is useless and PMU/interruption are
required.

## Baseline ladder (`memory_modes.py`)

The design calls for ten baselines to localize the bottleneck (storing vs.
finding the seed vs. traversing vs. validating vs. deciding to interrupt). The
prototype ships the two anchors needed to compute Memory Value with no external
services — `no_memory` (lower bound) and `full_context` (upper bound) — and names
the rest as explicit extension points: `sliding_window`, `vector_rag`,
`iterative_rag`, `temporal_kg`, `graph_plus_ledger`, `provider` (a configured
Hermes memory provider such as Hindsight), `always_on`, `selective_proactive` (a
distinct memory agent that decides whether to intervene), and `oracle`.

## How this maps onto Hermes (and the research gap it exposes)

Hermes' current external-memory surfacing is **query-conditioned prefetch**: each
turn recalls against the *previous user message* and injects a `<memory-context>`
block. That is passive retrieval keyed on lexical/semantic similarity to the
current text. PSMB's serendipitous-repurposing and analogical classes are
designed precisely to break that: the useful old episode shares little surface
text with the current task, so similarity-keyed prefetch misses it. The natural
next step in the *system* (separate from the benchmark) is a
`selective_proactive` mode — a background associative agent that runs bounded
spreading activation over an episodic ledger + condition-aware temporal KG +
open-question memory and injects an evidence-backed connection only when expected
value exceeds interruption cost. The benchmark and that system should be built
together; PSMB is the measurement half.

## Roadmap (beyond the prototype)

- **Tier 2** reconstructed historical discoveries (guard against pretrained
  leakage: obscure/post-cutoff cases, renamed entities, counterfactual worlds).
- **Tier 3** real lab trajectories (ELN + Slack + notebooks) with expert
  annotations of "an earlier result should have influenced this decision."
- Relational/NLI edge detector (replace the endpoints-mentioned proxy).
- The remaining eight baselines, especially `provider` (Hindsight) and
  `selective_proactive`.
- Open-question / hypothesis reactivation episodes (new evidence that resolves a
  previously stored open loop).

## Files at a glance
```
schema.py        data model + JSON (de)serialization
world.py         seeded chain/decoy generation + artifact rendering
generate.py      episode composition, signature episode, dataset IO
memory_modes.py  memory-mode policies (baselines)
runner.py        AgentClient protocol; Hermes + callable clients; run_episode
scoring.py       Layers A–D, PMU, Memory Value, RunLog/TurnRecord
mock_server.py   offline OpenAI-compatible endpoint (recall/myopic/noisy)
report.py        aggregation + text/JSON report
__main__.py      CLI: generate / score / run / demo
signature_episode.example.json   committed reference episode (inspectable)
```
