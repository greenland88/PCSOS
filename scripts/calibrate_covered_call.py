"""Governed entry point for ticker-specific covered-call calibration.

This command records readiness and promotion gates; it never promotes a
profile from a single-year result and never falls back to NVDA parameters.
"""
from __future__ import annotations

import argparse
import json
from pcs.research.covered_call_profiles import resolve_covered_call_profile

DATA_STATE = {"NVDA": "DATA_READY", "HOOD": "DATA_READY"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    symbol = args.symbol.upper()
    profile = resolve_covered_call_profile(symbol)
    data_state = DATA_STATE.get(symbol, "DATA_BLOCKED")
    result = {
        "module": "pcs.research.calibrate_covered_call",
        "version": "1.0", "symbol": symbol, "status": "RESEARCH_ONLY",
        "data_source": "PCS_CANONICAL_DATA", "data_state": data_state,
        "profile_status": profile.status.value,
        "promotion_gates": {
            "DATA_READY": data_state == "DATA_READY",
            "LIFECYCLE_VALID": False,
            "NO_ASSIGNMENT_VIOLATION": False,
            "CROSS_YEAR_EVIDENCE_AVAILABLE": False,
            "PROFILE_ROBUSTNESS_PASS": False,
        },
        "reason_codes": (["NVDA_V1_REFERENCE_REUSE"] if symbol == "NVDA" else
                          ["PROFILE_NOT_VALIDATED", "NO_NVDA_FALLBACK"]),
        "final_oos_read": False, "production_changes_allowed": False,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{symbol}: {result['profile_status']} ({data_state})")


if __name__ == "__main__":
    main()
