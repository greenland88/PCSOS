"""Print the canonical point-in-time NVDA covered-call decision."""
from __future__ import annotations

import argparse
import json
from datetime import date

from pcs.data.access import PCSDataAccess
from pcs.research import evaluate_covered_call


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate whether an NVDA call may be sold.")
    parser.add_argument("--as-of", default=str(date.today()), help="Decision date (YYYY-MM-DD).")
    parser.add_argument("--active-calls", type=int, default=0)
    args = parser.parse_args()
    result = evaluate_covered_call("NVDA", args.as_of,
                                   data_access=PCSDataAccess.canonical(),
                                   active_calls=args.active_calls)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
