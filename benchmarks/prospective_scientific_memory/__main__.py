"""PSMB command-line interface.

Subcommands:
  generate  -- write a dataset of episodes to disk
  score     -- score a single (episode, runlog) pair offline
  run       -- drive the real Hermes agent over a dataset (needs an endpoint)
  demo      -- self-contained end-to-end: mock endpoint + Hermes + scoring

Run with:  python -m benchmarks.prospective_scientific_memory <subcommand> ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from benchmarks.prospective_scientific_memory.generate import (
    generate_dataset,
    load_dataset,
)
from benchmarks.prospective_scientific_memory.report import build_report, write_report
from benchmarks.prospective_scientific_memory.schema import Episode
from benchmarks.prospective_scientific_memory.scoring import (
    EpisodeScore,
    RunLog,
    score_episode,
)

_DEFAULT_DATA = Path(__file__).parent / "data" / "dataset"


def _cmd_generate(args) -> int:
    eps = generate_dataset(args.n, seed0=args.seed0, out_dir=args.out,
                           include_signature=not args.no_signature)
    print(f"Wrote {len(eps)} episodes to {args.out}")
    return 0


def _cmd_score(args) -> int:
    ep = Episode.from_json(Path(args.episode).read_text(encoding="utf-8"))
    run = RunLog.from_dict(json.loads(Path(args.runlog).read_text(encoding="utf-8")))
    score = score_episode(ep, run, surface_threshold=args.surface_threshold)
    print(json.dumps(score.to_dict(), indent=2))
    return 0


def _run_dataset(episodes: List[Episode], *, modes: List[str], base_url: str,
                 model: str, api_key: str, provider: str, toolsets: List[str],
                 hermes_home: str, surface_threshold: float,
                 out_dir: Path) -> Dict[str, List[EpisodeScore]]:
    from benchmarks.prospective_scientific_memory.runner import (
        HermesAgentClient,
        run_episode,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    scores_by_mode: Dict[str, List[EpisodeScore]] = {m: [] for m in modes}
    for ep in episodes:
        for mode in modes:
            client = HermesAgentClient(
                model=model, base_url=base_url, api_key=api_key, provider=provider,
                toolsets=toolsets, hermes_home=hermes_home)
            run = run_episode(ep, client, mode=mode)
            (out_dir / f"runlog_{ep.id}_{mode}.json").write_text(
                json.dumps(run.to_dict(), indent=2), encoding="utf-8")
            sc = score_episode(ep, run, surface_threshold=surface_threshold)
            scores_by_mode[mode].append(sc)
            print(f"  [{ep.id}] mode={mode:<13} "
                  f"opp_rec={sc.trigger.opportunity_recall} "
                  f"pmu={sc.trigger.pmu} util={sc.task_utility}")
    return scores_by_mode


def _cmd_run(args) -> int:
    episodes = load_dataset(args.dataset)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    scores = _run_dataset(
        episodes, modes=modes, base_url=args.base_url, model=args.model,
        api_key=args.api_key, provider=args.provider,
        toolsets=[t for t in args.toolsets.split(",") if t],
        hermes_home=args.hermes_home, surface_threshold=args.surface_threshold,
        out_dir=args.out)
    report = build_report(scores)
    write_report(report, args.out)
    print("\n" + report["text"])
    return 0


def _cmd_demo(args) -> int:
    import tempfile

    from benchmarks.prospective_scientific_memory.mock_server import start_server

    tmp = Path(tempfile.mkdtemp(prefix="psmb_demo_"))
    hermes_home = str(tmp / "hermes_home")
    Path(hermes_home).mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out) if args.out else (tmp / "out")

    episodes = generate_dataset(args.n, seed0=args.seed0,
                                include_signature=not args.no_signature)
    httpd, port = start_server(0, policy=args.policy)
    base_url = f"http://127.0.0.1:{port}/v1"
    print(f"Mock endpoint ({args.policy}) at {base_url}; HERMES_HOME={hermes_home}")
    try:
        scores = _run_dataset(
            episodes, modes=["no_memory", "full_context"], base_url=base_url,
            model="psmb-mock", api_key="psmb", provider="custom",
            toolsets=["safe"], hermes_home=hermes_home,
            surface_threshold=args.surface_threshold, out_dir=out_dir)
    finally:
        httpd.shutdown()
    report = build_report(scores)
    write_report(report, out_dir)
    print("\n" + report["text"])
    print(f"\nArtifacts written to {out_dir}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m benchmarks.prospective_scientific_memory",
                                 description="Prospective Scientific Memory Benchmark")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write a dataset of episodes")
    g.add_argument("--n", type=int, default=8)
    g.add_argument("--seed0", type=int, default=1000)
    g.add_argument("--out", type=Path, default=_DEFAULT_DATA)
    g.add_argument("--no-signature", action="store_true")
    g.set_defaults(func=_cmd_generate)

    s = sub.add_parser("score", help="score one (episode, runlog) offline")
    s.add_argument("--episode", required=True)
    s.add_argument("--runlog", required=True)
    s.add_argument("--surface-threshold", type=float, default=0.5)
    s.set_defaults(func=_cmd_score)

    r = sub.add_parser("run", help="drive real Hermes over a dataset")
    r.add_argument("--dataset", type=Path, default=_DEFAULT_DATA)
    r.add_argument("--modes", default="no_memory,full_context")
    r.add_argument("--base-url", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--api-key", default="psmb")
    r.add_argument("--provider", default="custom")
    r.add_argument("--toolsets", default="safe")
    r.add_argument("--hermes-home", default=str(_DEFAULT_DATA.parent / "hermes_home"))
    r.add_argument("--surface-threshold", type=float, default=0.5)
    r.add_argument("--out", type=Path, default=_DEFAULT_DATA.parent / "runs")
    r.set_defaults(func=_cmd_run)

    d = sub.add_parser("demo", help="self-contained end-to-end demo (mock endpoint)")
    d.add_argument("--n", type=int, default=3)
    d.add_argument("--seed0", type=int, default=2000)
    d.add_argument("--policy", default="recall", choices=["recall", "myopic", "noisy"])
    d.add_argument("--no-signature", action="store_true")
    d.add_argument("--surface-threshold", type=float, default=0.5)
    d.add_argument("--out", type=Path, default=None)
    d.set_defaults(func=_cmd_demo)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
