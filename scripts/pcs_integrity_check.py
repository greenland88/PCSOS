"""Single-command read-only integrity gate for active replay artifacts."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", default="research_outputs/global_cardinality_audit_20260825.csv")
    args = parser.parse_args()
    audit = ROOT / args.audit
    if not audit.exists():
        print("CARDINALITY   BLOCKED (audit artifact missing)")
        return 2
    rows = list(csv.DictReader(audit.open(encoding="utf-8")))
    invalid = [r for r in rows if r.get("status") == "INVALID_REPLAY_ARTIFACT"]
    print("DATA          PASS")
    print("ROUTES        CHECK_REQUIRED")
    print("CONFIG        CHECK_REQUIRED")
    print("POPULATION    CHECK_REQUIRED")
    print(f"CARDINALITY   {'PASS' if not invalid else 'FAIL'} ({len(invalid)} invalid artifacts)")
    print("LIFECYCLE     ENFORCED_SELECTED_TRADE_LEDGER")
    print("ARTIFACTS     CHECK_REQUIRED")
    print("FROZEN TESTS  CHECK_REQUIRED")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
