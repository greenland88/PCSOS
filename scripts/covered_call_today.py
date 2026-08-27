"""Ticker-agnostic daily covered-call decision CLI."""
from __future__ import annotations

import argparse
import json
from pcs.data.access import PCSDataAccess
from pcs.research import evaluate_covered_call


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("--as-of")
    parser.add_argument("--active-calls", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = evaluate_covered_call(args.symbol.upper(), args.as_of or "2026-08-26",
                                   data_access=PCSDataAccess.canonical(),
                                   active_calls=args.active_calls)
    print(json.dumps(result, indent=2) if args.json else
          f"Decision: {result['decision']}\nReason: {result['decision_reason']}")


if __name__ == "__main__":
    main()
