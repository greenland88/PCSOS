"""Checkpointed, isolated NVDA corrected replay shards.

Each worker writes only its own year directory.  The script intentionally
keeps the production rules and canonical data read-only.
"""
from __future__ import annotations
import hashlib, json, os, time, uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions
from pcs.research.entry_candidate_universe import build_historical_setup_context_table, _atr14
from pcs.research.current_strategy_replay import build_lifecycle_quote_rows, validate_lifecycle_corporate_action
from pcs.research.stage4a_lifecycle import LifecycleAdapterError

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("PCS_NVDA_SHARD_OUT", str(ROOT / "research_outputs" / "nvda_price_basis_corrected_shards_20260824")))
YEARS = tuple(int(x) for x in os.environ.get("PCS_NVDA_YEARS", "2020 2021 2022 2023 2024 2025 2026").split())

def _strict_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "1", "yes"}:
            return True
        if value in {"false", "0", "no", ""}:
            return False
    return bool(value)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temp, index=False)
        if len(pd.read_parquet(temp)) != len(frame):
            raise RuntimeError("NVDA_SHARD_PARQUET_VALIDATION_FAILED")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def run_year(year: int) -> dict:
    target = OUT / f"year={year}"
    target.mkdir(parents=True, exist_ok=True)
    marker = target / "shard_summary.json"
    started = time.perf_counter()
    access = PCSDataAccess()
    registry = load_corporate_actions()
    identity_payload = {
        "year": year, "quote_start": str(max(pd.Timestamp(f"{year}-01-01"), pd.Timestamp("2020-01-02")).date()),
        "quote_end": str(min(pd.Timestamp(f"{year}-12-31") + pd.Timedelta(days=50), pd.Timestamp("2026-07-31")).date()),
        "daily": access.source_data_identity("daily", "NVDA"),
        "benchmark_daily": access.source_data_identity("daily", "QQQ"),
        "options": access.source_data_identity("options", "NVDA"),
        "code": {
            "runner": _digest(Path(__file__)),
            "candidate_universe": _digest(ROOT / "src/pcs/research/entry_candidate_universe.py"),
            "current_strategy_replay": _digest(ROOT / "src/pcs/research/current_strategy_replay.py"),
            "lifecycle_adapter": _digest(ROOT / "src/pcs/research/stage4a_lifecycle.py"),
        },
        "corporate_actions_sha256": _digest(ROOT / "config/data/corporate_actions.csv"),
    }
    identity = hashlib.sha256(json.dumps(identity_payload, sort_keys=True).encode()).hexdigest()
    if marker.exists():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
            candidate_path = target / "candidates.parquet"
            if (saved.get("identity") == identity
                    and saved.get("status") == "COMPLETED_QUOTE_ADAPTATION_ONLY"
                    and candidate_path.is_file()
                    and saved.get("candidates_sha256") == _digest(candidate_path)):
                return saved | {"resumed": True}
        except (OSError, ValueError, TypeError):
            pass
    start = pd.Timestamp(f"{year}-01-01")
    end = pd.Timestamp(f"{year}-12-31")
    # Include sufficient prior history for PIT indicators, but count only year.
    daily = access.read_prices("NVDA", "1999-01-01", end)
    benchmark = access.read_prices("QQQ", "1999-01-01", end)
    daily["date"] = pd.to_datetime(daily.date).dt.normalize()
    benchmark["date"] = pd.to_datetime(benchmark.date).dt.normalize()
    year_dates = daily.loc[daily.date.between(start, end), "date"].reset_index(drop=True)
    contexts = build_historical_setup_context_table(daily, benchmark, year_dates, "NVDA", "QQQ")
    feature_ready = sum(_strict_bool(x.get("available")) for x in contexts.values())
    setup_dates = [d for d, x in contexts.items() if _strict_bool(x.get("available")) and getattr(x.get("trend_gate_result"), "trend_gate_result", None) == "PASS" and getattr(x.get("pullback_gate_result"), "pullback_gate_result", None) == "PASS" and x.get("support_state") in {"moderate", "strong"} and _strict_bool(getattr(x.get("snapshot").cleanliness, "available", False))]
    quote_start = max(start, pd.Timestamp("2020-01-02"))
    quote_end = min(end + pd.Timedelta(days=50), pd.Timestamp("2026-07-31"))
    quotes = access.read_quotes("NVDA", quote_start, quote_end)
    if quotes.empty:
        raise RuntimeError("CANONICAL_OPTIONS_UNAVAILABLE")
    quotes["trade_date"] = pd.to_datetime(quotes.trade_date).dt.normalize()
    quotes["expiration_date"] = pd.to_datetime(quotes.expiration_date).dt.normalize()
    candidates = []
    for day in setup_dates:
        ctx = contexts[day]
        row = daily[daily.date.eq(day)].iloc[-1]
        atr = float(ctx["snapshot"].support.current_atr)
        close = float(ctx["snapshot"].pullback.current_close)
        factor = registry.adjustment_factor("NVDA", day, __import__("pcs.data.price_basis", fromlist=["PriceBasis"]).PriceBasis.MARKET_RAW, __import__("pcs.data.price_basis", fromlist=["PriceBasis"]).PriceBasis.ANALYTIC_ADJUSTED)
        chain = quotes[quotes.trade_date.eq(day) & quotes.call_put.astype(str).str.lower().eq("p")].copy()
        chain["dte"] = (chain.expiration_date - day).dt.days
        chain = chain[chain.dte.between(30, 45)]
        chain["comparison_strike"] = chain.strike / factor
        for exp, group in chain.groupby("expiration_date"):
            for _, short in group[(group.comparison_strike < close) & ((close-group.comparison_strike)/atr >= 2.3)].iterrows():
                for width in (5.0, 10.0, 2.0):
                    long = group[group.strike.eq(float(short.strike)-width)]
                    if long.empty: continue
                    long = long.iloc[0]
                    credit = float(short.bid-long.ask)
                    if credit <= 0 or credit/width < .10: continue
                    candidates.append({"date": day, "ticker":"NVDA", "expiration":exp, "short_strike":float(short.strike), "long_strike":float(long.strike), "comparison_short_strike":float(short.comparison_strike), "underlying":close, "atr":atr, "factor":factor, "credit":credit, "spread_width":width})
    frame = pd.DataFrame(candidates)
    _atomic_parquet(frame, target / "candidates.parquet")
    quote_rows_adapted = 0; lifecycle_failures = 0
    for candidate in candidates:
        try:
            validate_lifecycle_corporate_action(candidate, registry)
            q = quotes[(quotes.symbol.astype(str).str.upper() == "NVDA")
                       & quotes.call_put.astype(str).str.lower().eq("p")
                       & (quotes.trade_date >= pd.Timestamp(candidate["date"]))
                       & (quotes.trade_date <= min(pd.Timestamp(candidate["expiration"]), pd.Timestamp(candidate["date"])+pd.Timedelta(days=20)))
                       & quotes.expiration_date.eq(pd.Timestamp(candidate["expiration"]))
                       & quotes.strike.isin([candidate["short_strike"], candidate["long_strike"]])]
            candidate_id = (
                f"NVDA_{pd.Timestamp(candidate['date']).date()}_"
                f"{pd.Timestamp(candidate['expiration']).date()}_"
                f"{candidate['short_strike']}_{candidate['long_strike']}"
            )
            build_lifecycle_quote_rows(q, {**candidate, "candidate_id": candidate_id})
            quote_rows_adapted += 1
        except LifecycleAdapterError:
            lifecycle_failures += 1
    summary = {"year":year,"status":"COMPLETED_QUOTE_ADAPTATION_ONLY","identity":identity,"identity_inputs":identity_payload,"candidates_sha256":_digest(target / "candidates.parquet"),"trading_days":len(year_dates),"feature_ready_days":feature_ready,"setup_eligible_days":len(setup_dates),"candidate_spreads":len(candidates),"selected_entries":0,"quote_rows_adapted":quote_rows_adapted,"lifecycles_completed":0,"lifecycle_failures":lifecycle_failures,"seconds":round(time.perf_counter()-started,2),"price_basis_version":"price_basis_v1","corporate_action_version":"authoritative_corporate_action_registry_v1","data_source":"PCS_CANONICAL_DATA","resumed":False,"execution_semantics":"quote_adaptation_only_no_lifecycle_exit_or_pnl"}
    _atomic_json(marker, summary)
    return summary


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    workers = min(8, len(YEARS))
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_year, year): year for year in YEARS}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda x: x["year"])
    (OUT / "shard_manifest.json").write_text(json.dumps({"workers":workers,"years":results,"max_workers":8}, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"workers":workers,"years":results}, indent=2, default=str))


if __name__ == "__main__": main()
