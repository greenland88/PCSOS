"""Run the generic checkpointed candidate generator for one ticker."""
from __future__ import annotations
import argparse, json
from pcs.research.batched_candidate_generation import run_batched_candidates

def main():
    p = argparse.ArgumentParser(); p.add_argument("symbol"); p.add_argument("--daily", required=True); p.add_argument("--benchmark", required=True)
    p.add_argument("--start", required=True); p.add_argument("--end", required=True); p.add_argument("--output", required=True)
    p.add_argument("--workers", type=int, default=8); p.add_argument("--no-resume", action="store_true"); a = p.parse_args()
    result = run_batched_candidates(a.symbol, a.daily, a.benchmark, a.start, a.end, a.output, a.workers, not a.no_resume)
    print(json.dumps(result, indent=2, default=str)); return 0 if result.get("status") == "COMPLETE" else 1

if __name__ == "__main__": raise SystemExit(main())
