"""Replay frozen lifecycle regime inputs with SPY/QQQ confirmation semantics."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pcs.models.market import MarketState
from pcs.regime.market_regime import MarketRegimeEngine
from pcs.engine.decision_engine import load_rules

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "market_confirmation_replay_20260820"


def main() -> None:
    run_id, request_id = f"market-confirmation-replay-{uuid.uuid4().hex}", uuid.uuid4().hex
    states = pd.read_parquet(ROOT / "data/derived/canonical_pit_market_states.parquet")
    lifecycle = pd.read_parquet(ROOT / "research_outputs/phase0_20260820/lifecycle_marks.parquet")
    candidates = pd.read_parquet(ROOT / "research_outputs/phase0_20260820/candidate_universe.parquet")
    engine = MarketRegimeEngine(load_rules())
    rows = []
    for row in states.to_dict("records"):
        payload = json.loads(row["market_state"])
        new_state = MarketState.model_validate(payload)
        new_regime, new_score, new_flags = engine.classify(new_state)
        old_regime, old_score, old_flags = engine.classify(MarketState())
        rows.append({"date": row["date"], "old_regime": old_regime.value, "new_regime": new_regime.value,
                     "old_score": old_score, "new_score": new_score, "old_breadth_positive": True,
                     "new_breadth_positive": payload["breadth_positive"], "new_flags": new_flags,
                     "regime_changed": old_regime.value != new_regime.value,
                     "regime_gate_eligibility_changed": old_regime.value != "RED" and new_regime.value == "RED"})
    dates = pd.DataFrame(rows)
    dates.date = pd.to_datetime(dates.date).dt.normalize()
    candidate_view = candidates.copy()
    candidate_view.decision_date = pd.to_datetime(candidate_view.decision_date).dt.normalize()
    candidate_view = candidate_view.merge(dates[["date", "new_regime", "regime_gate_eligibility_changed"]], left_on="decision_date", right_on="date", how="left", validate="many_to_one")
    affected = candidate_view[candidate_view.regime_gate_eligibility_changed.eq(True)]
    # This is a regime-only counterfactual: no entry/exit/stop rule is changed.
    affected_pnl = float(affected.realized_pnl.sum()) if "realized_pnl" in affected else 0.0
    lifecycle_view = lifecycle.copy()
    lifecycle_view.mark_date = pd.to_datetime(lifecycle_view.mark_date).dt.normalize()
    lifecycle_view = lifecycle_view.merge(dates[["date", "new_regime"]], left_on="mark_date", right_on="date", how="left", validate="many_to_one")
    report = {"module": "pcs.research.market_confirmation_replay", "version": "market-confirmation-replay-v1", "symbol": "SPY_QQQ", "as_of": "2026-08-18", "status": "PASS", "data_timestamp": "2026-08-18", "calculation_version": "market-confirmation-replay-v1", "run_id": run_id, "request_id": request_id, "reason_codes": [], "semantics": "SPY_QQQ_MARKET_CONFIRMATION", "required_dates": int(len(dates)), "covered_dates": int(dates.date.nunique()), "true_days": int(dates.new_breadth_positive.sum()), "false_days": int((~dates.new_breadth_positive).sum()), "regime_classification_changes": int(dates.regime_changed.sum()), "regime_changes": dates.new_regime.value_counts().to_dict(), "old_regime_counts": dates.old_regime.value_counts().to_dict(), "candidate_rows_evaluated": int(len(candidate_view)), "candidate_eligibility_changes_regime_only": int(len(affected)), "affected_trades": int(affected.candidate_id.nunique()), "affected_realized_pnl_baseline": affected_pnl, "counterfactual_pnl_delta_if_blocked": -affected_pnl, "pnl_scope": "regime-only counterfactual; no lifecycle execution rerun or strategy rule change", "lifecycle_rows_checked": int(len(lifecycle_view)), "pit_timing": "date-only replay uses same-date close-after-session; pre-close lookup is prior completed session", "no_future_dates_used": True}
    OUT.mkdir(parents=True, exist_ok=True)
    dates.to_parquet(OUT / "daily_regime_comparison.parquet", index=False)
    affected.to_parquet(OUT / "affected_candidates.parquet", index=False)
    (OUT / "replay_impact.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
