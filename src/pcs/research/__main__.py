"""Single guarded entry point: python -m pcs.research run --spec PATH."""
from __future__ import annotations
import argparse, json
from .research_framework import ResearchSpecError
from .runner import ResearchRunner

def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m pcs.research")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--spec", required=True)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--real-preflight", action="store_true")
    run.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        runner = ResearchRunner.from_path(args.spec)
        result = runner.execute_research_replay() if args.execute else runner.real_preflight() if args.real_preflight else runner.dry_run()
    except ResearchSpecError as exc:
        print(json.dumps({"status": exc.status.value, "exact_reason": exc.reason}, indent=2))
        return 2
    print(json.dumps(result, indent=2, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
