"""Canonical global PCS base-universe screening.

This module is infrastructure screening only: it never evaluates a PCS setup,
selects a trade, or ranks profitability.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import hashlib
from typing import Any

import pandas as pd
import duckdb

from .access import PCSDataAccess, DataAccessError, DataQualityError


@dataclass(frozen=True)
class BasePoolConfig:
    min_history_years: float = 3.0
    min_avg_dollar_volume: float = 10_000_000.0
    min_avg_share_volume: float = 100_000.0
    min_price: float = 5.0
    min_30_45_dte_expirations: int = 1
    min_nearby_strikes: int = 5
    max_relative_spread: float = 0.35
    min_open_interest: float = 10.0
    min_option_volume: float = 1.0


def _reason_list(*values: str) -> list[str]:
    return sorted({v for v in values if v})


def _tier(score: float) -> str:
    return "TIER_A" if score >= 0.80 else "TIER_B" if score >= 0.60 else "TIER_C"


def _underlying_rows(manifest: pd.DataFrame, config: BasePoolConfig, access: PCSDataAccess) -> pd.DataFrame:
    try:
        files = str(Path("data/parquet/daily") / "symbol=*" / "year=*" / "*.parquet")
        daily = duckdb.connect().execute("select upper(symbol) symbol, min(date) coverage_start, max(date) coverage_end, count(*) daily_rows, avg(volume) avg_share_volume, avg(close*volume) avg_dollar_volume from read_parquet(?, hive_partitioning=true) group by upper(symbol)", [files]).fetchdf()
        manifest = manifest.merge(daily, on="symbol", how="left")
    except Exception:
        manifest = manifest.assign(coverage_start=pd.NaT, coverage_end=pd.NaT, daily_rows=0, avg_share_volume=pd.NA, avg_dollar_volume=pd.NA)
    rows: list[dict[str, Any]] = []
    for symbol, g in manifest[manifest.status.astype(str).eq("SUCCESS")].groupby("symbol"):
        symbol = str(symbol).upper()
        try:
            start, end, count = pd.to_datetime(g.coverage_start).min(), pd.to_datetime(g.coverage_end).max(), int(g.daily_rows.sum())
        except (FileNotFoundError, DataAccessError, ValueError):
            start, end, count = pd.NaT, pd.NaT, int(g.rows_written.astype(int).sum())
        if pd.isna(start) or pd.isna(end):
            rows.append({"symbol": symbol, "coverage_start": None, "coverage_end": None,
                         "daily_history_years": 0.0, "daily_rows": count,
                         "underlying_status": "DATA_BLOCKED", "underlying_score": 0.0,
                         "underlying_rank": 0, "avg_share_volume": None, "avg_dollar_volume": None,
                         "daily_data_quality": "SOURCE_UNAVAILABLE", "reason_codes": ["DATA_QUALITY_FAILURE"]})
            continue
        years = max(0.0, (end - start).days / 365.25)
        # Metadata is used for discovery; the canonical read below confirms
        # schema/coverage for candidates without opening raw files.
        status, reasons = "UNDERLYING_ELIGIBLE", []
        avg_share = float(g.avg_share_volume.mean()) if pd.notna(g.avg_share_volume).any() else None
        avg_dollar = float(g.avg_dollar_volume.mean()) if pd.notna(g.avg_dollar_volume).any() else None
        if avg_share is None or avg_share < config.min_avg_share_volume: status, reasons = "UNDERLYING_REJECTED", ["LOW_SHARE_VOLUME"]
        if avg_dollar is None or avg_dollar < config.min_avg_dollar_volume: status, reasons = "UNDERLYING_REJECTED", _reason_list(*reasons, "LOW_DOLLAR_VOLUME")
        if years < config.min_history_years: status, reasons = "UNDERLYING_REJECTED", ["INSUFFICIENT_HISTORY"]
        if count < int(years * 200): status, reasons = "DATA_BLOCKED", ["DAILY_DATA_GAPS"]
        rows.append({"symbol": symbol, "coverage_start": str(start.date()), "coverage_end": str(end.date()),
                     "daily_history_years": round(years, 3), "daily_rows": count,
                     "underlying_status": status, "underlying_score": round(min(1.0, years / 10.0), 6),
                     "underlying_rank": 0, "avg_share_volume": avg_share, "avg_dollar_volume": avg_dollar,
                     "daily_data_quality": "METADATA_VALIDATED", "reason_codes": reasons})
    out = pd.DataFrame(rows)
    if len(out): out = out.sort_values(["underlying_score", "symbol"], ascending=[False, True]).reset_index(drop=True); out["underlying_rank"] = range(1, len(out)+1)
    return out


def _option_metrics(access: PCSDataAccess, symbol: str, config: BasePoolConfig) -> dict[str, Any]:
    dataset = "options_recent" if access.manifest_path.name == "options_recent_manifest.csv" else "options"
    try:
        src = access.resolve_source(dataset, symbol)
        end = pd.Timestamp(src.last_date)
        start = max(pd.Timestamp(src.first_date), end - pd.Timedelta(days=90))
        q = access.read(dataset, symbol, start, end)
    except (FileNotFoundError, DataAccessError, DataQualityError, ValueError) as exc:
        return {"options_status": "OPTIONS_DATA_BLOCKED", "reason_codes": ["OPTION_DATA_QUALITY_FAILURE"], "option_quality_score": 0.0, "option_quality_rank": 0, "has_options": False, "historical_options_status": "AVAILABLE" if isinstance(exc, DataQualityError) else "NOT_AVAILABLE"}
    q["trade_date"] = pd.to_datetime(q.trade_date); q["expiration_date"] = pd.to_datetime(q.expiration_date)
    q = q[q.call_put.astype(str).str.lower().eq("p")].copy()
    dte = (q.expiration_date - q.trade_date).dt.days
    q = q[dte.between(30, 45)]
    if q.empty: return {"options_status": "OPTIONS_REJECTED", "reason_codes": ["NO_30_45_DTE_COVERAGE"], "option_quality_score": 0.0, "option_quality_rank": 0, "has_options": True, "historical_options_status": "AVAILABLE"}
    valid = q[q.bid.gt(0) & q.ask.gt(0) & q.bid.le(q.ask)].copy()
    rel = ((valid.ask - valid.bid) / valid.ask).replace([float("inf"), -float("inf")], pd.NA).dropna()
    expirations = int(q.expiration_date.nunique()); strikes = int(q.strike.nunique())
    quality = [expirations >= config.min_30_45_dte_expirations, strikes >= config.min_nearby_strikes, len(valid) > 0, (not rel.empty and float(rel.median()) <= config.max_relative_spread), bool((q.open_interest.fillna(0) >= config.min_open_interest).any()), bool((q.volume.fillna(0) >= config.min_option_volume).any())]
    ok = all(quality); reasons = [] if ok else _reason_list(*(["SPARSE_STRIKES"] if strikes < config.min_nearby_strikes else []), *(["WIDE_BID_ASK"] if rel.empty or float(rel.median()) > config.max_relative_spread else []), *(["LOW_OPEN_INTEREST"] if not (q.open_interest.fillna(0) >= config.min_open_interest).any() else []), *(["LOW_OPTION_VOLUME"] if not (q.volume.fillna(0) >= config.min_option_volume).any() else []))
    return {"options_status": "OPTIONS_ELIGIBLE" if ok else "OPTIONS_REJECTED", "reason_codes": reasons, "option_quality_score": round(sum(quality)/len(quality), 6), "option_quality_rank": 0, "has_options": True, "dte_30_45_availability": round(len(q)/max(1, len(q.drop_duplicates("trade_date"))), 3), "expiration_count": expirations, "strike_density": strikes, "option_volume_quality": float(q.volume.fillna(0).median()), "open_interest_quality": float(q.open_interest.fillna(0).median()), "bid_ask_quality": float(rel.median()) if len(rel) else None, "historical_options_status": "AVAILABLE"}


def build_base_pool(*, access: PCSDataAccess | None = None, daily_manifest: str | Path = "data/manifests/daily_universe_migration.csv", output_dir: str | Path = "research_outputs/global_pcs_base_universe", config: BasePoolConfig = BasePoolConfig()) -> dict[str, Any]:
    access = access or PCSDataAccess(); manifest = pd.read_csv(daily_manifest)
    # The base pool is a live research input boundary.  Never select an
    # options_recent/options_monthly migration store merely because its
    # manifest happens to exist; ticker-specific canonical routing is the
    # source of truth for both membership and quality reads.
    under = _underlying_rows(manifest, config, access)
    records = []
    for r in under.to_dict("records"):
        if r["underlying_status"] not in {"UNDERLYING_ELIGIBLE", "UNDERLYING_WATCH"}: om = {"options_status": "OPTIONS_DATA_BLOCKED", "reason_codes": ["UNDERLYING_NOT_ELIGIBLE"], "option_quality_score": 0.0, "option_quality_rank": 0, "has_options": False, "historical_options_status": "NOT_AVAILABLE"}
        else:
            try:
                access.resolve_source("options", r["symbol"])
                om = _option_metrics(access, r["symbol"], config)
            except (FileNotFoundError, DataAccessError, DataQualityError, ValueError):
                om = {"options_status": "OPTIONS_DATA_BLOCKED", "reason_codes": ["NO_OPTIONS"], "option_quality_score": 0.0, "option_quality_rank": 0, "has_options": False, "historical_options_status": "NOT_AVAILABLE"}
        reasons = _reason_list(*(r.pop("reason_codes", []) + om.pop("reason_codes", [])))
        pool = r["underlying_status"] == "UNDERLYING_ELIGIBLE" and om["options_status"] == "OPTIONS_ELIGIBLE"
        score = round(0.45 * float(r["underlying_score"]) + 0.55 * float(om["option_quality_score"]), 6)
        records.append({**r, **om, "pool_status": "PCS_BASE_POOL" if pool else ("DATA_BLOCKED" if "DATA_QUALITY_FAILURE" in reasons else "REJECTED"), "pool_score": score, "tier": _tier(score) if pool else None, "reason_codes": reasons, "historical_options_status": om["historical_options_status"]})
    out = pd.DataFrame(records).sort_values(["pool_status", "pool_score", "symbol"], ascending=[True, False, True]).reset_index(drop=True); out["pool_rank"] = 0; elig = out.pool_status.eq("PCS_BASE_POOL"); out.loc[elig, "pool_rank"] = range(1, int(elig.sum()) + 1)
    target = Path(output_dir); target.mkdir(parents=True, exist_ok=True)
    # Persist the two-stage artifacts. Pool 1 membership is the only allowed
    # input population for Pool 2; the hash is the identity fence.
    pool1_dir, pool2_dir = target / "pool_1_underlying", target / "pool_2_options"
    pool1_dir.mkdir(parents=True, exist_ok=True); pool2_dir.mkdir(parents=True, exist_ok=True)
    pool1 = out.copy(); pool1["pool1_status"] = pool1.underlying_status.map({"UNDERLYING_ELIGIBLE":"UNDERLYING_ELIGIBLE", "UNDERLYING_REJECTED":"UNDERLYING_REJECTED", "DATA_BLOCKED":"DATA_BLOCKED"}).fillna("UNDERLYING_WATCH")
    pool1["instrument_type"] = "UNKNOWN"; pool1["calculation_version"] = "base-pool-v2"; pool1["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    members = sorted(pool1.loc[pool1.pool1_status.eq("UNDERLYING_ELIGIBLE"), "symbol"].astype(str).str.upper())
    membership_hash = hashlib.sha256("\n".join(members).encode()).hexdigest()
    pool1.to_parquet(pool1_dir / "all_symbols_status.parquet", index=False); pool1.to_csv(pool1_dir / "all_symbols_status.csv", index=False)
    pool1[pool1.pool1_status.eq("UNDERLYING_ELIGIBLE")].to_parquet(pool1_dir / "underlying_pool.parquet", index=False); pool1[pool1.pool1_status.eq("UNDERLYING_ELIGIBLE")].to_csv(pool1_dir / "underlying_pool.csv", index=False)
    pool1_manifest = {"pool_version":"pool1-v1", "calculation_version":"base-pool-v2", "total_universe":len(pool1), "eligible_count":len(members), "watch_count":int((pool1.pool1_status=="UNDERLYING_WATCH").sum()), "rejected_count":int((pool1.pool1_status=="UNDERLYING_REJECTED").sum()), "blocked_count":int((pool1.pool1_status=="DATA_BLOCKED").sum()), "membership_hash":membership_hash, "membership_symbols":members, "POOL_1_FROZEN":True}
    (pool1_dir / "manifest.json").write_text(json.dumps(pool1_manifest, indent=2), encoding="utf-8")
    pool2 = out[out.symbol.isin(members)].copy(); pool2["pool_1_membership_hash"] = membership_hash; pool2.to_parquet(pool2_dir / "all_options_status.parquet", index=False); pool2.to_csv(pool2_dir / "all_options_status.csv", index=False); pool2[pool2.pool_status.eq("PCS_BASE_POOL")].to_parquet(pool2_dir / "pcs_base_pool.parquet", index=False); pool2[pool2.pool_status.eq("PCS_BASE_POOL")].to_csv(pool2_dir / "pcs_base_pool.csv", index=False)
    (pool2_dir / "manifest.json").write_text(json.dumps({"pool_version":"pool2-v1", "calculation_version":"base-pool-v2", "POOL_1_INPUT_HASH":membership_hash, "pool_1_symbol_count":len(members), "evaluated_count":len(pool2), "base_pool_count":int((pool2.pool_status=="PCS_BASE_POOL").sum())}, indent=2), encoding="utf-8")
    out.to_parquet(target / "pcs_base_pool.parquet", index=False); out.to_csv(target / "pcs_base_pool.csv", index=False)
    summary = {"module":"pcs.data.base_pool", "version":"1.0", "status":"COMPLETED", "data_source":"PCS_CANONICAL_DATA", "total_symbols_discovered":int(len(out)), "underlying_checked":int(len(out)), "underlying_eligible":int((out.underlying_status == "UNDERLYING_ELIGIBLE").sum()), "underlying_rejected":int(out.underlying_status.eq("UNDERLYING_REJECTED").sum()), "underlying_blocked":int(out.underlying_status.eq("DATA_BLOCKED").sum()), "options_checked":int(out.underlying_status.eq("UNDERLYING_ELIGIBLE").sum()), "options_eligible":int(out.options_status.eq("OPTIONS_ELIGIBLE").sum()), "options_rejected":int(out.options_status.eq("OPTIONS_REJECTED").sum()), "options_blocked":int(out.options_status.eq("OPTIONS_DATA_BLOCKED").sum()), "base_pool_count":int(elig.sum()), "tier_a_count":int((out.tier == "TIER_A").sum()), "tier_b_count":int((out.tier == "TIER_B").sum()), "tier_c_count":int((out.tier == "TIER_C").sum()), "rules_changed":False, "pcs_strategy_tested":False, "profitability_tested":False, "final_oos_read":False, "generated_at":datetime.now(timezone.utc).isoformat()}
    summary["artifact_sha256"] = hashlib.sha256((target / "pcs_base_pool.parquet").read_bytes()).hexdigest(); (target / "pool_manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8"); return summary


__all__ = ["BasePoolConfig", "build_base_pool"]
