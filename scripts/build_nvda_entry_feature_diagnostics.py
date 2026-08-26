"""Build PIT entry-state counts for the focused NVDA unified entries."""
from __future__ import annotations
import json
import hashlib
from collections import Counter
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.covered_call_decision import build_pit_entry_features

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/covered_call_nvda_unified_decision_evidence/entry_feature_diagnostics.json"


def main() -> None:
    daily = PCSDataAccess.canonical().read_prices("NVDA", end_date="2026-07-31")
    entries = []
    outcomes = {}
    for path in sorted((ROOT / "research_outputs").glob("covered_call_nvda_unified_*_v4/covered_call_entries.json")):
        value = json.loads(path.read_text())
        entries.extend(value.get("entries", []))
        for trade in (value.get("lifecycle") or {}).get("trades", []):
            outcomes.setdefault(str(trade.get("entry_date")), trade)
    enriched = []
    for entry in entries:
        feature = build_pit_entry_features(daily, as_of_date=entry["date"])
        if feature.get("status") == "PIT_SAFE":
            outcome = outcomes.get(str(entry["date"])[:10], {})
            enriched.append({**entry, **{k: feature.get(k) for k in
                ("extension20_atr", "momentum_state", "near_recent_high", "breakout_state", "iv_state")}})
            enriched[-1].update({k: outcome.get(k) for k in
                ("combined_pnl", "roll_count", "holding_days", "exit_state", "status")})
    def counts(key):
        return dict(sorted(Counter(str(x.get(key, "UNKNOWN")) for x in enriched).items()))
    def outcome_by(key):
        result = {}
        for value in sorted({str(x.get(key, "UNKNOWN")) for x in enriched}):
            group = [x for x in enriched if str(x.get(key, "UNKNOWN")) == value]
            joined = {}
            for x in group:
                if x.get("status") is not None:
                    joined.setdefault(str(x.get("date"))[:10], x)
            pnl = [float(x["combined_pnl"]) for x in joined.values() if x.get("combined_pnl") is not None]
            conflicts = sum(1 for x in joined.values() if str(x.get("status", "")).upper() == "HARD_CONSTRAINT_CONFLICT")
            result[value] = {"entries": len(group), "joined_episodes": len(joined),
                             "completed_episodes": len(pnl),
                             "average_combined_pnl": sum(pnl) / len(pnl) if pnl else None,
                             "conflict_rate": None,
                             "conflict_episodes_joined": conflicts,
                             "conflict_rate": conflicts / len(joined) if joined else None}
        return result
    def outcome_by_combo(keys):
        def value_for(row, key):
            if key == "extension20_atr":
                value = row.get(key)
                if value is None:
                    return "UNKNOWN"
                value = float(value)
                return ("<0.5" if value < .5 else ".5-1" if value < 1 else
                        "1-1.5" if value < 1.5 else "1.5-2" if value < 2 else ">2")
            return str(row.get(key, "UNKNOWN"))
        result = {}
        labels = sorted({"|".join(value_for(x, k) for k in keys) for x in enriched})
        for label in labels:
            group = [x for x in enriched if "|".join(value_for(x, k) for k in keys) == label]
            joined = {}
            for x in group:
                if x.get("status") is not None:
                    joined.setdefault(str(x.get("date"))[:10], x)
            pnl = [float(x["combined_pnl"]) for x in joined.values() if x.get("combined_pnl") is not None]
            result[label] = {"entries": len(group), "joined_episodes": len(joined),
                             "completed_episodes": len(pnl),
                             "average_combined_pnl": sum(pnl) / len(pnl) if pnl else None,
                             "total_combined_pnl": sum(pnl) if pnl else 0.0,
                             "conflict_episodes_joined": sum(1 for x in joined.values() if str(x.get("status", "")).upper() == "HARD_CONSTRAINT_CONFLICT"),
                             "conflict_rate": (sum(1 for x in joined.values() if str(x.get("status", "")).upper() == "HARD_CONSTRAINT_CONFLICT") / len(joined) if joined else None)}
        return result
    result = {"module": "pcs.research.nvda_entry_feature_diagnostics", "version": "1.0",
              "symbol": "NVDA", "status": "COMPLETED", "data_source": "PCS_CANONICAL_DATA",
              "unified_lifecycle_only": True, "entries": len(enriched),
              "extension20_atr": {label: sum(1 for x in enriched if (
                  (-float("inf") if label == "<0.5" else .5 if label == ".5-1" else 1 if label == "1-1.5" else 1.5 if label == "1.5-2" else 2) <= x["extension20_atr"] < (
                  .5 if label == "<0.5" else 1 if label == ".5-1" else 1.5 if label == "1-1.5" else 2 if label == "1.5-2" else float("inf")))) for label in ("<0.5", ".5-1", "1-1.5", "1.5-2", ">2")},
              "momentum_state": counts("momentum_state"), "breakout_state": counts("breakout_state"),
              "iv_state": counts("iv_state"),
              "outcome_join_status": "JOINED_BY_ENTRY_DATE_FROM_UNIFIED_LIFECYCLE",
              "outcome_by_momentum": outcome_by("momentum_state"),
              "outcome_by_breakout": outcome_by("breakout_state"),
              "outcome_by_extension_momentum": outcome_by_combo(("extension20_atr", "momentum_state")),
              "outcome_by_momentum_breakout": outcome_by_combo(("momentum_state", "breakout_state")),
              "reason_codes": ["PIT_FEATURES_REBUILT", "UNIFIED_ENTRY_ARTIFACTS_ONLY",
                               "OUTCOMES_NOT_IN_ENTRY_ROWS", "NO_AUTOMATIC_PROMOTION"],
              "final_oos_read": False, "production_changes_allowed": False}
    OUT.write_text(json.dumps(result, indent=2))
    manifest = {"research_id": "covered_call_nvda_entry_feature_diagnostics",
                "status": "CURRENT", "current": True, "data_source": "PCS_CANONICAL_DATA",
                "ticker": "NVDA", "final_oos_read": False,
                "production_changes_allowed": False,
                "files": {OUT.name: hashlib.sha256(OUT.read_bytes()).hexdigest()},
                "reason_codes": ["PIT_FEATURES_REBUILT", "UNIFIED_LIFECYCLE_ONLY",
                                 "NO_AUTOMATIC_PROMOTION"]}
    OUT.with_name("entry_feature_diagnostics_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"status": result["status"], "entries": result["entries"], "output": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
