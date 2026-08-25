"""Read-only non-tech readiness audit and current PCS inventory."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
NONTECH = "JPM BAC GS MS WMT COST HD MCD UNH ABBV LLY JNJ CAT RTX GE DE XOM CVX COP".split()
SECTORS = {"JPM": "Financials", "BAC": "Financials", "GS": "Financials", "MS": "Financials", "WMT": "Consumer Staples", "COST": "Consumer Staples", "HD": "Consumer Discretionary", "MCD": "Consumer Discretionary", "UNH": "Healthcare", "ABBV": "Healthcare", "LLY": "Healthcare", "JNJ": "Healthcare", "CAT": "Industrials", "RTX": "Industrials", "GE": "Industrials", "DE": "Industrials", "XOM": "Energy", "CVX": "Energy", "COP": "Energy"}
PRIORITY = ["JPM", "BAC", "GS", "COST", "HD", "CAT", "CVX", "MCD"]
KNOWN = {"AMD": "CONDITIONAL_POSITIVE", "AMZN": "CONDITIONAL_POSITIVE", "TSLA": "NEGATIVE", "NVDA": "INSUFFICIENT_DATA", "SPY": "ROBUST_POSITIVE", "QQQ": "NEGATIVE"}


def daily_range(symbol: str) -> tuple[str | None, str | None, int]:
    paths = sorted((Path("data/parquet/daily") / f"symbol={symbol}").rglob("*.parquet"))
    if not paths:
        return None, None, 0
    x = pd.concat((pd.read_parquet(p, columns=["date"]) for p in paths), ignore_index=True)
    x.date = pd.to_datetime(x.date)
    return str(x.date.min().date()), str(x.date.max().date()), len(x)


def options_range(symbol: str) -> tuple[str | None, str | None]:
    p = Path("data/manifests/storage_manifest.csv")
    if not p.exists():
        return None, None
    x = pd.read_csv(p)
    x = x[x.symbol.eq(symbol)]
    if x.empty:
        return None, None
    return str(pd.to_datetime(x.min_date).min().date()), str(pd.to_datetime(x.max_date).max().date())


def nontech_audit() -> list[dict]:
    rows = []
    for symbol in NONTECH:
        d0, d1, n = daily_range(symbol)
        o0, o1 = options_range(symbol)
        rows.append({
            "symbol": symbol, "sector": SECTORS[symbol], "daily_start": d0, "daily_end": d1, "daily_rows": n,
            "options_history_start": o0, "options_history_end": o1, "canonical_v2_available": False,
            "liquidity_available": False, "candidate_generation_feasibility": "BLOCKED_OPTIONS_SOURCE_MISSING",
            "lifecycle_replay_feasibility": "BLOCKED_OPTIONS_SOURCE_MISSING", "vendor_source_completeness": "MISSING",
            "duplicate_conflict_risk": "NOT_ASSESSED_OPTIONS_MISSING", "estimated_migration_complexity": "HIGH",
            "classification": "NEEDS_V2_BUILD" if n >= 5000 else "DATA_PARTIAL",
            "priority_score": 1 if symbol in PRIORITY else 2,
        })
    return rows


def inventory() -> list[dict]:
    universe = json.loads(json.dumps([]))
    import yaml
    universe = yaml.safe_load(Path("config/market_universe.yaml").read_text(encoding="utf-8"))["pcs_universe"]
    phase0 = Path("research_outputs/phase0_20260820")
    rows = []
    for symbol in universe:
        d0, d1, n = daily_range(symbol)
        o0, o1 = options_range(symbol)
        candidate = (phase0 / "candidate_universe.parquet").exists() and symbol in set(pd.read_parquet(phase0 / "candidate_universe.parquet", columns=["ticker"]).ticker)
        if symbol in {"SPY", "QQQ"}:
            candidate = (ROOT / f"{symbol}_entry_contract_v2.parquet").exists()
        lifecycle = (phase0 / "lifecycle_marks.parquet").exists() and symbol in set(pd.read_parquet(phase0 / "lifecycle_marks.parquet", columns=["ticker"]).ticker)
        if symbol in {"SPY", "QQQ"}:
            lifecycle = (ROOT / f"{symbol}_lifecycle_marks.parquet").exists()
        rows.append({"symbol": symbol, "sector": SECTORS.get(symbol, "Technology" if symbol not in SECTORS else SECTORS[symbol]), "active_data_route": "config-routed" if symbol in {"QQQ", "NVDA", "AMD", "TSLA", "AMZN"} else "UNROUTED", "daily_start": d0, "daily_end": d1, "options_start": o0, "options_end": o1, "candidate_artifact_exists": bool(candidate), "lifecycle_artifact_exists": bool(lifecycle), "oos_status": KNOWN.get(symbol, "NOT_RUN"), "pcs_classification": KNOWN.get(symbol, "NOT_RUN"), "production_research_status": "RESEARCH_ONLY"})
    return rows


def main() -> None:
    out = {"module": "nontech_readiness_and_pcs_inventory", "version": "20260821.v1", "nontech": nontech_audit(), "inventory": inventory(), "recommended_next_batch": PRIORITY, "notes": ["Read-only audit; no options ingestion, route changes, candidate generation, or replay performed.", "SPY/QQQ TRAIN+VALIDATION metrics are sourced from train_validation_report.json; FINAL OOS was not run."]}
    target = Path("research_outputs/pcs_overnight_status_20260821")
    target.mkdir(parents=True, exist_ok=True)
    (target / "nontech_readiness_and_inventory.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__": main()
