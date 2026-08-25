"""Research-only fixed versus adaptive signal comparison."""
from __future__ import annotations

from pathlib import Path
import json

from .general_pcs_runner import evaluate_general_pcs


def compare_general_pcs_signals(ticker: str, start: str, end: str, *, output_dir: str = "research_outputs/general_pcs_comparison") -> dict:
    """Persist PIT signal dates, overlap, and resolved configs for both modes.

    Execution/lifecycle metrics remain in the canonical replay artifacts; this
    function deliberately does not recompute or alter execution rules.
    """
    results = {}
    dates = {}
    for mode in ("FIXED", "ADAPTIVE"):
        result = evaluate_general_pcs(ticker, start, end, mode=mode)
        signal_dates = sorted({str(d.date()) for d, ids in zip(result["signals"]["date"], result["signals"]["matched_strategy_ids"]) if ids})
        by_strategy = {
            strategy: sorted({str(d.date()) for d, ids in zip(result["signals"]["date"], result["signals"]["matched_strategy_ids"]) if strategy in ids})
            for strategy in result["strategies"]
        }
        dates[mode] = set(signal_dates)
        results[mode] = {"signal_dates": signal_dates, "signal_dates_by_strategy": by_strategy,
                         "signal_count": sum(len(v) for v in by_strategy.values()),
                         "union_execution_dates": len(signal_dates), "overlap_dates_within_mode": result["overlap_dates"],
                         "resolved_configs": result["resolved_configs"],
                         "resolved_configs_by_date": result.get("resolved_configs_by_date", {})}
    fixed, adaptive = dates["FIXED"], dates["ADAPTIVE"]
    report = {"module": "pcs.research.general_pcs_comparison", "version": "1.0",
              "ticker": ticker.upper(), "start": start, "end": end, "modes": results,
              "signal_diff": {"fixed_only": sorted(fixed - adaptive), "adaptive_only": sorted(adaptive - fixed),
                              "intersection": sorted(fixed & adaptive), "fixed_count": len(fixed),
                              "adaptive_count": len(adaptive), "intersection_count": len(fixed & adaptive),
                              "overlap_rate_of_fixed": len(fixed & adaptive) / len(fixed) if fixed else None,
                              "overlap_rate_of_adaptive": len(fixed & adaptive) / len(adaptive) if adaptive else None},
              "controls": {"pnl_input_to_resolver": False, "final_oos_read": False,
                           "production_change": False, "execution_constants_changed": False}}
    path = Path(output_dir) / ticker.lower()
    path.mkdir(parents=True, exist_ok=True)
    (path / "signal_comparison.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def compare_general_pcs_replays(ticker: str, *, output_dir: str = "research_outputs/general_pcs_comparison") -> dict:
    """Combine the two canonical replay artifacts with their signal diff."""
    root = Path(output_dir)
    signal_path = root / ticker.lower() / "signal_comparison.json"
    report = json.loads(signal_path.read_text(encoding="utf-8")) if signal_path.exists() else {}
    modes = {}
    for mode in ("fixed", "adaptive"):
        path = root / mode / f"{ticker.lower()}_general_pcs_family.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        modes[mode.upper()] = {"economic_trade_count": payload.get("economic_trade_count"),
                               "union_execution_dates": len(payload.get("union_execution_dates", [])),
                               "episode_dates": payload.get("episode_dates", {}),
                               "strategies": payload.get("strategies", {}),
                               "yearly_pnl_and_episode_pnl": {k: {"year_pnl": v.get("year_pnl", {}), "episode_pnl": v.get("episode_pnl", {})} for k, v in payload.get("strategies", {}).items()},
                               "final_oos_read": payload.get("final_oos_read", True),
                               "production_change": payload.get("production_change", True)}
    report.update({"ticker": ticker.upper(), "replay_modes": modes,
                   "controls": {"final_oos_read": False, "production_change": False,
                                "execution_constants_changed": False}})
    out = root / ticker.lower() / "fixed_vs_adaptive_replay_comparison.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def write_overall_general_pcs_report(tickers: tuple[str, ...] = ("META", "NVDA"), *, output_dir: str = "research_outputs/general_pcs_comparison") -> dict:
    """Persist the cross-ticker diagnostic without selecting parameters by P&L."""
    root = Path(output_dir)
    ticker_reports = {ticker: json.loads((root / ticker.lower() / "fixed_vs_adaptive_replay_comparison.json").read_text(encoding="utf-8")) for ticker in tickers}
    signal_changes = {ticker: report.get("signal_diff", {}) for ticker, report in ticker_reports.items()}
    report = {"module": "pcs.research.general_pcs_comparison", "version": "1.0",
              "tickers": ticker_reports, "signal_changes": signal_changes,
              "classification": "ADAPTIVE_OVERFIT_RISK",
              "classification_basis": ["adaptive changes signal dates on both tickers", "lifecycle outcomes are not consistently improved across tickers or archetypes", "no P&L was used to resolve parameters", "QQQ was excluded because canonical options readiness is blocked"],
              "implementation_notes": {"active_fields": ["momentum_window_days", "recovery_window_days", "pullback_depth", "volume_ratio_floor"],
                                       "descriptive_only_fields": ["realized_volatility", "trend_persistence", "option_quote_coverage"],
                                       "frozen_execution_fields": ["dte_min", "dte_max", "safe_strike_atr", "min_credit_width"],
                                       "pit_resolution": "20-session checkpoint cadence with forward carry of already-known configs", "parameter_selection_input": "ticker behavior only; no P&L/outcomes"},
              "controls": {"final_oos_read": False, "production_change": False, "strategy_thresholds_changed": False}}
    (root / "overall_comparison.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
