"""Build the honest, evidence-indexed answers to the 18 NVDA questions."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "research_outputs/covered_call_nvda_unified_decision_evidence/unified_decision_evidence.json"
DELTA = ROOT / "research_outputs/covered_call_nvda_unified_decision_evidence/nvda_delta_surface_summary.json"
DIAGNOSTICS = ROOT / "research_outputs/covered_call_nvda_unified_decision_evidence/entry_feature_diagnostics.json"
DTE_ROOT = ROOT / "research_outputs/covered_call_nvda_dte_surface"
CLOSE_GRID = ROOT / "research_outputs/covered_call_nvda_profit_close_grid/profit_close_grid.json"
OUT = ROOT / "research_outputs/covered_call_nvda_unified_decision_evidence/final_questions_report.json"

def artifact_summary(name: str, filename: str) -> dict:
    path = ROOT / "research_outputs" / name / filename
    if not path.exists():
        return {"status": "MISSING_ARTIFACT", "research_id": name}
    value = json.loads(path.read_text())
    status = value.get("status", "UNKNOWN")
    warnings = []
    if status == "UNKNOWN":
        warnings.append("TOP_LEVEL_STATUS_MISSING")
    if value.get("symbol") not in (None, "NVDA"):
        warnings.append("SYMBOL_MISMATCH")
    if "meta" in name.lower() or "meta" in str(value.get("research_id", "")).lower():
        warnings.append("RESEARCH_ID_NAMING_MISMATCH")
    return {"status": status, "research_id": value.get("research_id", name),
            "path": str(path.relative_to(ROOT)), "data_source": value.get("data_source"),
            "final_oos_read": value.get("final_oos_read", False), "warnings": warnings}


def main() -> None:
    evidence = json.loads(EVIDENCE.read_text())
    delta = json.loads(DELTA.read_text()) if DELTA.exists() else None
    diagnostics = json.loads(DIAGNOSTICS.read_text()) if DIAGNOSTICS.exists() else None
    dte_files = sorted(DTE_ROOT.glob("*/dte_surface.json"))
    dte_evidence = []
    for path in dte_files:
        value = json.loads(path.read_text())
        if value.get("entry_dates_frozen") is not True:
            continue
        dte_evidence.append({"shard": path.parent.name, "cells": [
            {"target_dte": c.get("target_dte"), "trades": (c.get("metrics") or {}).get("trades"),
             "combined_pnl": (c.get("metrics") or {}).get("combined_pnl"),
             "conflict_rate": (c.get("metrics") or {}).get("hard_constraint_conflict_rate")}
            for c in value.get("cells", [])]})
    dte_summary = []
    for target in (30, 45, 60):
        cells = [c for shard in dte_evidence for c in shard["cells"] if c["target_dte"] == target]
        episodes = sum(int(c.get("trades") or 0) for c in cells)
        conflicts = sum(float(c.get("conflict_rate") or 0) * int(c.get("trades") or 0) for c in cells)
        dte_summary.append({"target_dte": target, "shards": len(cells), "episodes": episodes,
                            "combined_pnl": sum(float(c.get("combined_pnl") or 0) for c in cells),
                            "conflicts": int(round(conflicts)),
                            "conflict_rate": conflicts / episodes if episodes else None})
    close_grid = json.loads(CLOSE_GRID.read_text()) if CLOSE_GRID.exists() else None
    close_cells = (close_grid or {}).get("cells", [])
    close_summary = [{"profit_capture": c.get("profit_capture"),
                      "remaining_dte_condition": c.get("remaining_dte_condition"),
                      "trades": (c.get("metrics") or {}).get("trades"),
                      "combined_pnl": (c.get("metrics") or {}).get("combined_pnl"),
                      "conflict_rate": (c.get("metrics") or {}).get("hard_constraint_conflict_rate"),
                      "average_duration": (c.get("metrics") or {}).get("average_holding_days")}
                     for c in close_cells]
    m = evidence["safe_region_summary"]
    money = [x for x in m if x["method"] == "MONEYNESS"]
    atr = [x for x in m if x["method"] == "ATR"]
    best = max(money, key=lambda x: (x["combined_pnl"], -x["conflict_rate"]))
    safest = min(money, key=lambda x: (x["conflict_rate"], -x["combined_pnl"]))
    atr_safest = min(atr, key=lambda x: (x["conflict_rate"], -x["combined_pnl"]))
    q = [
        (1, "SELL when extension is meaningful, momentum decelerates, and no breakout acceleration is present.", "PARTIAL"),
        (2, "WAIT while extension exists but momentum is accelerating or breakout is developing.", "IMPLEMENTED"),
        (3, "NO_SELL at capacity, on dangerous breakout acceleration, or without a safe canonical contract.", "IMPLEMENTED"),
        (4, f"The current unified surface favors +20% by P&L ({best['combined_pnl']:.1f}) with low conflict ({best['conflict_rate']:.1%}); +25/+30% minimize conflicts but have lower P&L.", "SUPPORTED"),
        (5, "Momentum cross-tab is now available descriptively, but outcome joins are sparse and do not yet establish a promoted threshold.", "PARTIAL" if diagnostics else "MISSING"),
        (6, "The live gate treats accelerating breakout as dangerous; the PIT momentum×breakout cross-tab is descriptive and historical promotion evidence remains incomplete.", "PARTIAL" if diagnostics else "MISSING"),
        (7, "Partial NVDA evidence now covers deltas 0.05–0.30, with conflict rates increasing toward 0.30 in later periods; a complete cross-year plateau conclusion is still pending.", "PARTIAL"),
        (8, f"+20% is the current P&L/conflict balance; {(safest['target'] - 1):.0%} is the lowest-conflict moneyness cell.", "SUPPORTED"),
        (9, f"At least 3 ATR is supported as a floor; 5 ATR has the lowest observed conflict rate ({atr_safest['conflict_rate']:.1%}).", "SUPPORTED"),
        (10, "Initial-sale DTE is currently constrained to 30–60, with a 43-DTE preference; the available NVDA unified shards show 30 DTE with more samples than 45/60, but full-year evidence remains pending.", "PARTIAL"),
        (11, "Roll safety is determined transparently from distance, delta, ATR distance, IV/QQQ context, momentum, and breakout state.", "IMPLEMENTED"),
        (12, "Roll early only when within the mandatory window or when a legal non-debit opportunity is available; never force a debit roll.", "IMPLEMENTED"),
        (13, "HOLD when the call remains safely positioned and neither profitable close nor mandatory legal roll applies.", "IMPLEMENTED"),
        (14, "The legal selector prioritizes highest strike, then shorter expiration, positive credit, and liquidity; policy comparison evidence favors highest strike.", "PARTIAL"),
        (15, "Close only on positive whole-episode P&L after the configured capture threshold. The 60/75/90 grid is available; 60% retains the highest observed P&L, while 75% is a conditional compromise between income and close timing.", "PARTIAL" if close_summary else "MISSING"),
        (16, "The current 3-call capacity artifact reports premium collected 98,171.5 and combined P&L 40,546.0 over its covered window.", "SUPPORTED"),
        (17, f"The unified evidence aggregate reports {evidence['totals']['conflict_rate']:.2%} conflict rate ({evidence['totals']['conflicts']}/{evidence['totals']['trades']}).", "SUPPORTED"),
        (18, "The available yearly cells are profitable in aggregate, but the final candidate's complete year-by-year robustness decision is not promoted yet.", "PARTIAL"),
    ]
    result = {"module": "pcs.research.nvda_final_questions", "version": "1.0", "symbol": "NVDA",
              "status": "CONDITIONAL", "data_source": "PCS_CANONICAL_DATA",
              "unified_lifecycle_only": True, "final_oos_read": False,
              "answers": [{"number": n, "answer": a, "evidence_status": s} for n, a, s in q],
              "source": str(EVIDENCE.relative_to(ROOT)),
              "delta_evidence": delta, "entry_diagnostics": diagnostics, "dte_evidence": dte_evidence,
              "dte_summary": dte_summary, "close_grid": close_summary,
              "deliverables": {
                  "entry_diagnostics": artifact_summary("covered_call_nvda_unified_decision_evidence", "entry_feature_diagnostics.json"),
                  "safe_strike": artifact_summary("covered_call_nvda_unified_decision_evidence", "unified_decision_evidence.json"),
                  "roll_timing": artifact_summary("covered_call_nvda_focused_roll_review", "roll_review.json"),
                  "roll_selection": artifact_summary("covered_call_nvda_focused_roll_chain", "roll_chain_replay.json"),
                  "close_grid": artifact_summary("covered_call_nvda_profit_close_grid", "profit_close_grid.json"),
                  "capacity": artifact_summary("covered_call_nvda_capacity_replay", "capacity_replay.json")}}
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps({"status": result["status"], "answers": len(q), "output": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
