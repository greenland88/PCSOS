"""Focused NVDA roll review under the frozen no-debit/ITM/120-DTE rules."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "research_outputs/covered_call_nvda_full_baseline_v2/covered_call_entries.json"
OUT = ROOT / "research_outputs/covered_call_nvda_focused_roll_review"

def main() -> None:
    access = PCSDataAccess.canonical()
    report = json.loads(REPORT.read_text())
    entries = {str(pd.Timestamp(x["date"]).date()): x for x in report["entries"]}
    conflicts = [x for x in report["lifecycle"]["trades"]
                 if x.get("exit_state") == "HARD_CONSTRAINT_CONFLICT"]
    rows = []
    for trade in conflicts:
        entry = entries.get(str(pd.Timestamp(trade["entry_date"]).date()))
        if not entry:
            continue
        review = str(pd.Timestamp(trade["exit_date"]).date())
        old_exp = pd.Timestamp(entry["expiration"]).date()
        try:
            q = access.read_quotes_for_windows(
                "NVDA", [(review, review)],
                columns=["symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask", "delta", "open_interest", "volume"])
        except Exception as exc:
            rows.append({"entry_date": trade["entry_date"], "review_date": review,
                         "status": "DATA_CONFLICT", "reason": str(exc)})
            continue
        q = q[q.call_put.astype(str).str.lower().isin({"c", "call"})].copy()
        q["expiration_date"] = pd.to_datetime(q.expiration_date).dt.date
        old = q[(q.expiration_date == old_exp) & (q.strike == float(entry["strike"]))]
        if old.empty:
            rows.append({"entry_date": trade["entry_date"], "review_date": review,
                         "status": "OLD_QUOTE_MISSING"})
            continue
        old_ask = float(old.iloc[0].ask)
        candidates = q[(q.expiration_date > old_exp) & q.bid.notna() & q.ask.notna()].copy()
        candidates["new_dte"] = (pd.to_datetime(candidates.expiration_date) - pd.Timestamp(review)).dt.days
        candidates["net_roll_credit"] = (candidates.bid.astype(float) - old_ask) * 100
        candidates = candidates[(candidates.new_dte >= 14) & (candidates.new_dte <= 120)]
        legal = candidates[candidates.net_roll_credit >= 0].copy()
        highest = legal.sort_values(["strike", "net_roll_credit"], ascending=[False, False])
        shortest = legal.sort_values(["new_dte", "strike", "net_roll_credit"], ascending=[True, False, False])
        if not legal.empty:
            legal["balanced_score"] = (legal.strike / float(entry["strike"]) * 2.0
                                        - legal.new_dte / 120.0
                                        + legal.net_roll_credit / max(float(legal.net_roll_credit.max()), 1.0))
        balanced = legal.sort_values(["balanced_score", "strike"], ascending=[False, False])
        rows.append({"entry_date": trade["entry_date"], "review_date": review,
                     "old_expiration": str(old_exp), "old_strike": float(entry["strike"]),
                     "candidate_count": int(len(candidates)), "legal_candidate_count": int(len(legal)),
                     "itm_legal_count": int((legal.strike < float(trade.get("underlying_close", entry["strike"]))).sum()) if not legal.empty else 0,
                     "best_highest_strike": highest.iloc[0].to_dict() if not highest.empty else None,
                     "best_shortest_extension": shortest.iloc[0].to_dict() if not shortest.empty else None,
                     "best_balanced": balanced.iloc[0].to_dict() if not balanced.empty else None,
                     "status": "LEGAL_ROLL_AVAILABLE" if not legal.empty else "CONSTRAINT_CONFLICT",
                     "reason_codes": ["H3_NET_CREDIT_GE_ZERO", "H4_ITM_ALLOWED", "H5_MAX_120_DTE"]})
    result = {"module": "pcs.research.nvda_focused_roll_review", "version": "1.0",
              "symbol": "NVDA", "status": "COMPLETED", "data_source": "PCS_CANONICAL_DATA",
              "episodes_reviewed": len(rows), "rows": rows, "final_oos_read": False,
              "production_changes_allowed": False}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "roll_review.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({"episodes_reviewed": len(rows),
                      "legal_rolls": sum(x.get("status") == "LEGAL_ROLL_AVAILABLE" for x in rows),
                      "conflicts": sum(x.get("status") == "CONSTRAINT_CONFLICT" for x in rows)}, indent=2))

if __name__ == "__main__":
    main()
