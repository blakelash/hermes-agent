"""Aggregate PSMB episode scores into a readable report + machine-readable JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from benchmarks.prospective_scientific_memory.scoring import (
    EpisodeScore,
    pairwise_memory_value,
)


def _mean(xs: List[float]) -> float:
    return round(sum(xs) / max(1, len(xs)), 4)


def aggregate(scores_by_mode: Dict[str, List[EpisodeScore]]) -> Dict:
    out: Dict[str, Dict] = {}
    for mode, scores in scores_by_mode.items():
        out[mode] = {
            "n_episodes": len(scores),
            "opportunity_recall": _mean([s.trigger.opportunity_recall for s in scores]),
            "intervention_precision": _mean(
                [s.trigger.intervention_precision for s in scores]),
            "mean_timeliness": _mean([s.trigger.mean_timeliness for s in scores]),
            "false_injections": _mean([s.trigger.false_injections for s in scores]),
            "interruption_burden": _mean(
                [s.trigger.interruption_burden for s in scores]),
            "pmu": _mean([s.trigger.pmu for s in scores]),
            "evidence_recall": _mean([s.integrity.evidence_recall for s in scores]),
            "condition_fidelity": _mean(
                [s.integrity.condition_fidelity for s in scores]),
            "invalidated_suppression": _mean(
                [s.integrity.invalidated_suppression for s in scores]),
            "task_utility": _mean([s.task_utility for s in scores]),
        }
    return out


def render_text(agg: Dict, mem_value: Optional[Dict] = None) -> str:
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("Prospective Scientific Memory Benchmark -- report")
    lines.append("=" * 72)
    cols = ["opportunity_recall", "intervention_precision", "mean_timeliness",
            "pmu", "evidence_recall", "task_utility", "false_injections",
            "interruption_burden"]
    short = {"opportunity_recall": "opp_rec", "intervention_precision": "int_prec",
             "mean_timeliness": "timely", "pmu": "PMU", "evidence_recall": "ev_rec",
             "task_utility": "util", "false_injections": "false", 
             "interruption_burden": "interrupt"}
    header = f"{'mode':<16}" + "".join(f"{short[c]:>10}" for c in cols)
    lines.append(header)
    lines.append("-" * len(header))
    for mode, m in agg.items():
        row = f"{mode:<16}" + "".join(f"{m[c]:>10}" for c in cols)
        lines.append(row)
    if mem_value:
        lines.append("")
        lines.append(f"Memory Value ({mem_value['with_mode']} - "
                     f"{mem_value['without_mode']}): "
                     f"{mem_value['mean_memory_value']:+.4f}  "
                     f"(util_with={mem_value['mean_utility_with']}, "
                     f"util_without={mem_value['mean_utility_without']}, "
                     f"n={mem_value['n']})")
    lines.append("=" * 72)
    return "\n".join(lines)


def build_report(scores_by_mode: Dict[str, List[EpisodeScore]], *,
                 with_mode: str = "full_context",
                 without_mode: str = "no_memory") -> Dict:
    agg = aggregate(scores_by_mode)
    mv = None
    if with_mode in scores_by_mode and without_mode in scores_by_mode:
        mv = pairwise_memory_value(scores_by_mode, with_mode=with_mode,
                                   without_mode=without_mode)
    return {"aggregate": agg, "memory_value": mv,
            "text": render_text(agg, mv),
            "per_episode": {mode: [s.to_dict() for s in scores]
                            for mode, scores in scores_by_mode.items()}}


def write_report(report: Dict, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "report.txt").write_text(report["text"], encoding="utf-8")
    return out_dir
