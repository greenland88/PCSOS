"""Research-only single-factor strict-entry comparison on TRAIN+VALIDATION."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pcs.research.entry_candidate_universe import build_historical_setup_context

ROOT = Path("research_outputs/strict_entry_variants_20260821")
PHASE0 = Path("research_outputs/phase0_20260820/candidate_universe.parquet")
INDEX = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
VARIANTS = {
    "CURRENT_BASELINE": lambda x: pd.Series(True, index=x.index),
    "STRONG_TREND_ONLY": lambda x: x.trend_state.eq("A"),
    "STRONG_SUPPORT_ONLY": lambda x: x.support_state.eq("strong"),
    "STRONG_TREND_AND_SUPPORT": lambda x: x.trend_state.eq("A") & x.support_state.eq("strong"),
    "HIGH_PREDICTABILITY_ONLY": lambda x: x.predictability_state.eq("clean"),
    "MARKET_CONFIRMATION_STRICT": lambda x: x.market_confirmation.eq(True) & x.market_confirmation_pit.eq("PIT_SAFE"),
    "NO_WEAK_SUPPORT_ENTRIES": lambda x: x.support_state.ne("weak"),
}


def _daily(symbol: str) -> pd.DataFrame:
    paths = sorted((Path("data/parquet/daily") / f"symbol={symbol}").rglob("*.parquet"))
    x = pd.concat((pd.read_parquet(p) for p in paths), ignore_index=True)
    x.date = pd.to_datetime(x.date).dt.normalize()
    return x.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _splits(symbol: str) -> list[dict]:
    if symbol in {"SPY", "QQQ"}:
        return json.loads((INDEX / "split_manifest.json").read_text(encoding="utf-8"))["splits"][symbol][:2]
    return json.loads((Path("research_outputs/oos_splits_20260821") / f"{symbol}.json").read_text(encoding="utf-8"))["splits"][:2]


def _market() -> pd.DataFrame:
    x = pd.read_parquet("data/derived/market_confirmation_daily.parquet")
    x.date = pd.to_datetime(x.date).dt.normalize()
    return x.rename(columns={"date": "decision_date", "breadth_positive": "market_confirmation", "pit_status": "market_confirmation_pit"})[["decision_date", "market_confirmation", "market_confirmation_pit"]].drop_duplicates("decision_date")


def _contexts(symbol: str, dates: pd.Series) -> pd.DataFrame:
    stock = _daily(symbol)
    benchmark = _daily("QQQ")
    rows = []
    for day in sorted(pd.to_datetime(dates).dt.normalize().unique()):
        ctx = build_historical_setup_context(stock, benchmark, day, symbol, "QQQ")
        rows.append({"ticker": symbol, "decision_date": pd.Timestamp(day), "trend_state": ctx.get("trend_state"), "support_state": ctx.get("support_state"), "predictability_state": ctx.get("predictability_state"), "context_available": bool(ctx.get("available", False))})
    return pd.DataFrame(rows)


def _load(symbol: str) -> pd.DataFrame:
    if symbol in {"SPY", "QQQ"}:
        c = pd.read_parquet(INDEX / f"{symbol}_entry_contract_v2.parquet", columns=["candidate_id", "decision_date"])
        o = pd.read_parquet(INDEX / f"{symbol}_train_validation_outcomes.parquet")
        x = c.merge(o, on=["candidate_id", "decision_date"], how="inner")
        x["ticker"] = symbol
    else:
        x = pd.read_parquet(PHASE0)
        x = x[x.ticker.eq(symbol) & x.status.eq("COMPLETE")].copy()
        x = x.rename(columns={"realized_pnl": "pnl"})
        x["candidate_id"] = x.candidate_id.astype(str)
        x["decision_date"] = pd.to_datetime(x.decision_date)
        x["stop"] = x.stop_triggered.fillna(False).astype(bool)
    x["decision_date"] = pd.to_datetime(x.decision_date).dt.normalize()
    x = x.merge(_contexts(symbol, x.decision_date), on=["ticker", "decision_date"], how="left")
    x = x.merge(_market(), on="decision_date", how="left")
    return x


def _metrics(x: pd.DataFrame) -> dict:
    p = pd.to_numeric(x.pnl, errors="coerce").dropna()
    w, l = p[p > 0], p[p < 0]
    curve = p.reset_index(drop=True).cumsum()
    return {"trade_count": int(len(p)), "trade_reduction_pct": None, "expectancy": float(p.mean()) if len(p) else None, "profit_factor": float(w.sum() / abs(l.sum())) if len(l) and l.sum() else None, "win_rate_pct": float((p > 0).mean() * 100) if len(p) else None, "stop_rate_pct": float(x.stop.mean() * 100) if len(x) else None, "avg_loss": float(l.mean()) if len(l) else None, "worst_trade": float(p.min()) if len(p) else None, "max_drawdown": float((curve - curve.cummax()).min()) if len(p) else None, "total_pnl": float(p.sum()) if len(p) else None, "tail_loss_5pct_count": int((p <= p.quantile(.05)).sum()) if len(p) else None}


def main() -> None:
    all_rows = []
    for symbol in ["SPY", "AMD", "AMZN", "TSLA", "QQQ", "NVDA"]:
        x = _load(symbol)
        for split in _splits(symbol):
            a, b = pd.Timestamp(split["start"]), pd.Timestamp(split["end"])
            y = x[x.decision_date.between(a, b)].copy()
            base_n = len(y)
            for name, predicate in VARIANTS.items():
                try:
                    keep = predicate(y).fillna(False)
                    z = y[keep].copy()
                    m = _metrics(z)
                    m.update({"ticker": symbol, "split": split["name"], "variant": name, "baseline_trade_count": base_n, "trade_reduction_pct": round((base_n - len(z)) / base_n * 100, 4) if base_n else None})
                    all_rows.append(m)
                except (AttributeError, KeyError):
                    all_rows.append({"ticker": symbol, "split": split["name"], "variant": name, "status": "UNAVAILABLE_FACTOR_COLUMN", "baseline_trade_count": base_n})
    frame = pd.DataFrame(all_rows)
    ROOT.mkdir(parents=True, exist_ok=True)
    frame.to_csv(ROOT / "strict_entry_variant_metrics.csv", index=False)
    # A variant is classified descriptively from validation only; no optimization or promotion.
    val = frame[frame.split.eq("VALIDATION") & frame.variant.ne("CURRENT_BASELINE")].copy()
    summary = []
    for variant, g in val.groupby("variant"):
        b = frame[(frame.split.eq("VALIDATION")) & frame.variant.eq("CURRENT_BASELINE")].merge(g[["ticker", "expectancy", "profit_factor", "stop_rate_pct", "trade_count"]], on="ticker", suffixes=("_base", "_variant"))
        b["expectancy_change"] = b.expectancy_variant - b.expectancy_base
        b["pf_change"] = b.profit_factor_variant - b.profit_factor_base
        b["stop_change"] = b.stop_rate_pct_variant - b.stop_rate_pct_base
        positive = int((b.expectancy_change > 0).sum())
        summary.append({"variant": variant, "tickers_available": int(len(b)), "expectancy_improved_tickers": positive, "expectancy_change_mean": float(b.expectancy_change.mean()) if len(b) else None, "pf_change_mean": float(b.pf_change.mean()) if len(b) else None, "stop_rate_change_mean": float(b.stop_change.mean()) if len(b) else None, "trade_reduction_mean_pct": float(g.trade_reduction_pct.mean()) if len(g) else None, "cross_ticker_classification": "ROBUST_IMPROVEMENT" if len(b) and positive == len(b) and (b.pf_change > 0).all() else "PROMISING" if positive > len(b) / 2 else "TRADEOFF" if positive and (b.stop_change < 0).sum() else "NO_IMPROVEMENT"})
    result = {"module": "strict_entry_variant_research", "version": "20260821.v1", "data_scope": "TRAIN_AND_VALIDATION_ONLY", "final_oos_run": False, "rules_changed": False, "threshold_optimization": False, "variant_definitions": {"STRONG_TREND_ONLY": "trend_state == A", "STRONG_SUPPORT_ONLY": "support_state == strong", "STRONG_TREND_AND_SUPPORT": "trend_state == A AND support_state == strong", "HIGH_PREDICTABILITY_ONLY": "predictability_state == clean", "MARKET_CONFIRMATION_STRICT": "market_confirmation == true AND PIT_SAFE", "NO_WEAK_SUPPORT_ENTRIES": "support_state != weak"}, "summary_validation": summary, "ticker_metrics_path": "strict_entry_variant_metrics.csv"}
    (ROOT / "strict_entry_variant_summary.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__": main()
