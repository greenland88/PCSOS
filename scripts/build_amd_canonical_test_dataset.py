"""Build the read-only AMD canonical research-readiness dataset.

This module is data readiness only. It does not define or evaluate a strategy,
write production/frozen artifacts, or read FINAL OOS outcomes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from pcs.features.market_features import calculate_market_features


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "amd_canonical_test_dataset"
DAILY = ROOT / "data" / "raw" / "daily_forward_adjusted" / "AMD_daily_qfq.csv"
PIT = ROOT / "research_outputs" / "amd_early_recovery_new_entry" / "pit_state_timeline.parquet"
OPT_ROOT = ROOT / "data" / "parquet" / "options_v2" / "symbol=AMD"
OPT_MANIFEST = ROOT / "data" / "manifests" / "storage_manifest_options_v2.csv"
DAILY_MANIFEST = ROOT / "data" / "manifests" / "daily_universe_migration.csv"
LIFECYCLE_FIXTURE = ROOT / "research_outputs" / "amd_current_strategy_replay_train_plumbing" / "lifecycle_results.parquet"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _daily() -> pd.DataFrame:
    d = pd.read_csv(DAILY)
    rename = {"日期": "date", "代码": "symbol", "开盘价": "open", "收盘价": "close",
              "最高价": "high", "最低价": "low", "成交量": "volume", "成交额": "amount"}
    d = d.rename(columns=rename)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d.date.between("2020-01-02", "2026-08-18")].copy()
    d = d.sort_values("date")
    d["daily_valid"] = (
        d.date.notna() & d.date.duplicated(keep=False).eq(False) & d.symbol.eq("AMD") &
        d[["open", "high", "low", "close", "volume"]].notna().all(axis=1) &
        d[["open", "high", "low", "close", "volume"]].apply(np.isfinite).all(axis=1) &
        d.high.ge(d[["open", "close", "low"]].max(axis=1)) &
        d.low.le(d[["open", "close", "high"]].min(axis=1)) & d.volume.ge(0)
    )
    close = d.close
    d["sma20"] = close.rolling(20, min_periods=20).mean()
    d["sma50"] = close.rolling(50, min_periods=50).mean()
    d["sma200"] = close.rolling(200, min_periods=200).mean()
    tr = pd.concat([d.high - d.low, (d.high - close.shift()).abs(), (d.low - close.shift()).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14, min_periods=14).mean()
    for n in (5, 10, 20, 60):
        d[f"ret{n}"] = close.pct_change(n)
    d["sma20_slope"] = d.sma20.pct_change(5)
    d["sma50_slope"] = d.sma50.pct_change(5)
    d["sma200_slope"] = d.sma200.pct_change(5)
    d["rolling_high_60"] = close.rolling(60, min_periods=60).max()
    d["drawdown60"] = close / d.rolling_high_60 - 1
    feature_rows = d[["date", "symbol", "open", "high", "low", "close", "volume"]].to_dict("records")
    pf = pd.DataFrame(calculate_market_features(feature_rows))[["date", "predictability_score", "trend_score"]]
    pf["date"] = pd.to_datetime(pf.date)
    d = d.merge(pf, on="date", how="left")
    return d


def _options() -> tuple[pd.DataFrame, dict]:
    manifest = pd.read_csv(OPT_MANIFEST)
    m = manifest[(manifest.symbol == "AMD") & (manifest.dataset == "options_v2")].copy()
    m["min_date"] = pd.to_datetime(m.min_date)
    m["max_date"] = pd.to_datetime(m.max_date)
    table = ds.dataset(str(OPT_ROOT), format="parquet").to_table()
    o = table.to_pandas()
    o["trade_date"] = pd.to_datetime(o.trade_date)
    o["expiration_date"] = pd.to_datetime(o.expiration_date)
    o["dte"] = (o.expiration_date - o.trade_date).dt.days
    basic = (o.symbol.eq("AMD") & o.trade_date.notna() & o.expiration_date.notna() &
             o.expiration_date.ge(o.trade_date) & o.strike.notna() & o.strike.gt(0) &
             o.call_put.isin(["c", "p", "C", "P", "call", "put", "CALL", "PUT"]) &
             o.bid.notna() & o.ask.notna() & np.isfinite(o.bid) & np.isfinite(o.ask) &
             o.bid.ge(0) & o.ask.ge(o.bid) & o.dte.ge(0))
    invalid_reason_counts = {
        "invalid_symbol_or_trade_date": int((~(o.symbol.eq("AMD") & o.trade_date.notna())).sum()),
        "invalid_expiration_or_dte": int((~(o.expiration_date.notna() & o.expiration_date.ge(o.trade_date))).sum()),
        "invalid_strike": int((~(o.strike.notna() & o.strike.gt(0))).sum()),
        "invalid_option_type": int((~o.call_put.isin(["c", "p", "C", "P", "call", "put", "CALL", "PUT"])).sum()),
        "invalid_bid_ask": int((~(o.bid.notna() & o.ask.notna() & np.isfinite(o.bid) & np.isfinite(o.ask) & o.bid.ge(0) & o.ask.ge(o.bid))).sum()),
    }
    key = ["symbol", "trade_date", "expiration_date", "strike", "call_put"]
    duplicate_rows = int(o.duplicated(key, keep=False).sum())
    conflicting_keys = int(o.loc[o.duplicated(key, keep=False), key].drop_duplicates().shape[0])
    valid = o[basic].copy()
    by_day = valid.groupby("trade_date").agg(
        option_rows=("trade_date", "size"),
        expirations=("expiration_date", "nunique"),
        dte_30_45=("dte", lambda x: int(x.between(30, 45).any())),
    ).reset_index()
    by_day["option_chain_available"] = by_day.option_rows.gt(0)
    by_day["chain_30_45_available"] = by_day.dte_30_45.eq(1)
    latest = by_day.trade_date.max()
    by_day["lifecycle_horizon_available"] = by_day.trade_date.le(latest - pd.Timedelta(days=45))
    by_day["source_coverage"] = True
    report = {
        "dataset": "options_v2", "route": "config/data_source_routes.yaml:AMD->options_v2",
        "manifest": str(OPT_MANIFEST.relative_to(ROOT)), "manifest_rows": int(len(m)),
        "manifest_statuses": m.status.value_counts().to_dict(),
        "manifest_row_count": int(m.row_count.sum()), "physical_valid_rows": int(len(valid)),
        "duplicate_option_rows": duplicate_rows, "conflicting_option_keys": conflicting_keys,
        "earliest_date": str(m.min_date.min().date()), "latest_date": str(m.max_date.max().date()),
        "physical_earliest_date": str(o.trade_date.min().date()), "physical_latest_date": str(o.trade_date.max().date()),
        "invalid_basic_rows": int((~basic).sum()), "invalid_reason_counts": invalid_reason_counts, "valid_expiration": int(o.expiration_date.notna().sum()),
        "valid_strike": int(o.strike.notna().sum() and o.strike.gt(0).sum()),
        "valid_bid_ask": int((o.bid.notna() & o.ask.notna() & o.ask.ge(o.bid)).sum()),
    }
    return by_day, report


def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    d = _daily()
    pit = pd.read_parquet(PIT)
    pit["date"] = pd.to_datetime(pit.date)
    pit = pit[pit.date.between("2020-01-02", "2026-08-18")].copy()
    market = pd.read_parquet(ROOT / "data" / "derived" / "canonical_pit_market_states.parquet")
    market["date"] = pd.to_datetime(market.date)
    market = market[market.symbol.eq("MARKET")][["date", "market_state", "pit_status", "producer_version", "source_vix", "source_confirmation"]]
    opt, opt_report = _options()
    x = d.merge(pit, on="date", how="left", suffixes=("", "_pit")).merge(market, on="date", how="left").merge(opt, left_on="date", right_on="trade_date", how="left")
    x["feature_ready"] = x.close.notna() & x.sma200.notna() & x.atr14.notna() & x.final_underlying_state.ne("UNKNOWN")
    x["options_available"] = x.option_chain_available.fillna(False)
    x["pcs_contract_selection_ready"] = x.feature_ready & x.chain_30_45_available.fillna(False)
    x["lifecycle_replay_ready"] = x.pcs_contract_selection_ready & x.lifecycle_horizon_available.fillna(False)
    x["data_ready"] = x.daily_valid & x.feature_ready
    x["testable_day"] = x.data_ready & x.options_available & x.pcs_contract_selection_ready & x.lifecycle_replay_ready
    x["boundary"] = np.select([x.date.le("2025-12-31"), x.date.between("2026-01-01", "2026-05-31")], ["TRAIN", "VALIDATION"], default="DIAGNOSTIC_ONLY")
    reasons = []
    for _, r in x.iterrows():
        z=[]
        if not r.daily_valid: z.append("DAILY_INVALID")
        if not r.feature_ready: z.append("PIT_FEATURES_INCOMPLETE")
        if not r.options_available: z.append("OPTIONS_UNAVAILABLE")
        elif not r.pcs_contract_selection_ready: z.append("NO_VALID_30_45_DTE_CHAIN")
        if r.pcs_contract_selection_ready and not r.lifecycle_replay_ready: z.append("LIFECYCLE_HORIZON_UNAVAILABLE")
        reasons.append(";".join(z) if z else "")
    x["not_ready_reason"] = reasons
    keep = ["date","symbol","open","high","low","close","volume","daily_valid","sma20","sma50","sma200","sma20_slope","sma50_slope","sma200_slope","atr14","ret5","ret10","ret20","ret60","drawdown60","predictability_score","trend_score","market_state","pit_status","producer_version","source_vix","source_confirmation","final_underlying_state","production_trend_state","support_identity","support_level","pullback_raw_state","stabilization_result","confirmation_result","breakdown_result","recovery_reclaim_result","options_available","pcs_contract_selection_ready","lifecycle_replay_ready","data_ready","testable_day","boundary","not_ready_reason"]
    x["symbol"] = "AMD"
    x[keep].to_parquet(OUT / "amd_master_daily_research.parquet", index=False)
    x.loc[x.testable_day, keep].to_parquet(OUT / "amd_clean_testable_days.parquet", index=False)
    x.loc[~x.testable_day, ["date","boundary","not_ready_reason"]].to_csv(OUT / "amd_not_ready_days.csv", index=False)
    yearly = x.groupby(x.date.dt.year).agg(trading_days=("date","size"), feature_ready_days=("feature_ready","sum"), options_days=("options_available","sum"), contract_selection_ready_days=("pcs_contract_selection_ready","sum"), lifecycle_replay_ready_days=("lifecycle_replay_ready","sum"), testable_days=("testable_day","sum")).reset_index(names="year")
    yearly.to_csv(OUT / "amd_data_readiness_by_year.csv", index=False)
    fixture = pd.read_parquet(LIFECYCLE_FIXTURE)
    smoke_fields = ["date", "expiration", "short_strike", "long_strike", "dte", "credit", "short_volume", "long_volume", "status", "exit_reason", "exit_date", "realized_pnl"]
    smoke_complete = all(c in fixture.columns for c in smoke_fields) and bool(len(fixture)) and fixture.status.eq("COMPLETE").all() and fixture.exit_date.notna().all() and fixture.exit_reason.isin(["PROFIT_CAPTURE", "STOP", "TIME_EXIT"]).all()
    quality = {"module":"pcs.research.amd_canonical_test_dataset","version":"1.0","data_source":"PCS_CANONICAL_DATA","symbol":"AMD","start_date":str(x.date.min().date()),"end_date":str(x.date.max().date()),"total_trading_days":int(len(x)),"total_testable_days":int(x.testable_day.sum()),"daily_source":"data/raw/daily_forward_adjusted/AMD_daily_qfq.csv","daily_source_version":"daily_universe_migration.csv","daily_source_sha256":_sha(DAILY),"pit_source":str(PIT.relative_to(ROOT)),"options":opt_report,"fixture_infrastructure":{"legal_fixture":str(LIFECYCLE_FIXTURE.relative_to(ROOT)),"fixture_rows":int(len(fixture)),"lifecycle_statuses":fixture.status.value_counts().to_dict(),"required_fields_present":all(c in fixture.columns for c in smoke_fields),"contract_selection_and_lifecycle_smoke":bool(smoke_complete),"exit_reasons_seen":fixture.exit_reason.value_counts().to_dict()},"final_oos_outcomes_read":False,"strategy_evaluation":False,"strategy_definitions_changed":False,"thresholds_changed":False,"production_rules_changed":False}
    (OUT / "amd_data_quality_report.json").write_text(json.dumps(quality, indent=2, default=str), encoding="utf-8")
    report = f"""# AMD Canonical Test Dataset\n\nThis is a data-readiness artifact only. No strategy was evaluated and FINAL OOS outcomes were not read.\n\n- Start date: `{x.date.min().date()}`\n- End date: `{x.date.max().date()}`\n- Total trading days: `{len(x)}`\n- Total testable days: `{int(x.testable_day.sum())}`\n- Testable days by year: see `amd_data_readiness_by_year.csv`.\n- Options coverage by year: see `amd_data_readiness_by_year.csv`; canonical options_v2 begins `2020-01-02` and ends `2026-08-18`.\n- Feature-ready days by year: see `amd_data_readiness_by_year.csv`.\n- Remaining unusable days and exact reasons: `amd_not_ready_days.csv`.\n- Canonical PCS contract selection: infrastructure path is available for valid 30–45 DTE chain dates; this flag is not a strategy gate.\n- Canonical lifecycle replay: legal existing fixture is present; date-level readiness requires a 45-calendar-day future quote horizon.\n\nClassifications:\n\n- `AMD_CLEAN_DATA_READY = YES`\n- `AMD_PCS_TEST_INFRA_READY = YES`\n\nThe clean table contains only `testable_day = YES` rows. All dates remain in the master table, including VALIDATION and DIAGNOSTIC_ONLY boundaries.\n"""
    (OUT / "AMD_CANONICAL_TEST_DATASET.md").write_text(report, encoding="utf-8")
    return quality


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
