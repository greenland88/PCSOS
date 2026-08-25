"""V2 from-scratch broad NVDA PCS outcome map.

This is descriptive research only.  It starts from the complete PIT-ready
TRAIN calendar and deliberately does not apply production entry gates.
"""
from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions, PriceBasis
from pcs.research.underlying_state import evaluate_as_of, UnderlyingState
from pcs.research.current_strategy_replay import build_lifecycle_quote_rows, validate_lifecycle_corporate_action, _identity
from pcs.research.stage4a_lifecycle import Stage4ALifecycleReplayAdapter, LifecycleAdapterError
from pcs.research.variant_b_replay import ReplayPolicy
from pcs.research.entry_candidate_universe import _atr14

V2_VERSION = "nvda-entry-discovery-v2-broad-outcome-map-v1"

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

def _parquet_safe(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in out.columns:
        if out[column].map(lambda x: isinstance(x, (list, dict, tuple))).any():
            out[column] = out[column].map(lambda x: json.dumps(x, default=str) if isinstance(x, (list, dict, tuple)) else x)
    return out


def build_broad_outcome_map(output_dir: str | Path = "research_outputs/nvda_entry_discovery_agent_v2",
                            start: str = "2020-01-02", end: str = "2023-12-31") -> dict:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    access = PCSDataAccess(); ticker = "NVDA"
    daily = access.read_prices(ticker, start, end).copy()
    daily.date = pd.to_datetime(daily.date).dt.normalize()
    # Safe-strike distance must use the same Wilder ATR implementation as the
    # canonical replay/production trend indicators.
    daily["atr_14"] = _atr14(daily)
    states = [evaluate_as_of(daily, ticker, day) for day in daily.date]
    state_df = pd.DataFrame(states)
    ready = state_df[(state_df.available_data.map(_strict_bool)) &
                     state_df.final_underlying_state.ne(UnderlyingState.UNKNOWN.value)].copy()
    registry = load_corporate_actions()
    rows = []
    lifecycle_rows = []
    for s in ready.to_dict("records"):
        day = pd.Timestamp(s["date"]).normalize(); chain = access.read_option_chain(ticker, day)
        chain.trade_date = pd.to_datetime(chain.trade_date).dt.normalize()
        chain.expiration_date = pd.to_datetime(chain.expiration_date).dt.normalize()
        atr_value = float(daily.loc[daily.date.eq(day), "atr_14"].iloc[0]) if pd.notna(daily.loc[daily.date.eq(day), "atr_14"].iloc[0]) else 0.0
        rec = {"trade_date": day, "ticker": ticker, "pit_feature_ready": True,
               "option_chain_available": bool(len(chain)), "valid_dte_available": False,
               "standardized_pcs_constructable": False, "liquidity_status": "NOT_EVALUATED",
               "credit_status": "NOT_EVALUATED", "event_state": "NOT_EVALUATED",
               "executable_pcs": False, "reason_code": ""}
        if chain.empty:
            rec["reason_code"] = "NO_OPTION_DATA"; rows.append(rec); continue
        puts = chain[chain.call_put.astype(str).str.lower().eq("p")].copy()
        puts["dte"] = (puts.expiration_date - day).dt.days
        puts = puts[puts.dte.between(30, 45)].copy()
        rec["valid_dte_available"] = bool(len(puts))
        if puts.empty:
            rec["reason_code"] = "NO_VALID_DTE"; rows.append(rec); continue
        close = float(s["close"]); atr = atr_value
        if not atr or atr <= 0:
            # Standardized construction needs a PIT risk scale; do not invent one.
            rec["reason_code"] = "NO_PIT_ATR"; rows.append(rec); continue
        # One deterministic, shared-path-compatible construction: nearest exact
        # $5 PCS whose short put is at least 2.3 ATR below the PIT close.
        candidates = []
        raw_width = 5.0 * registry.adjustment_factor(ticker, day, PriceBasis.MARKET_RAW, PriceBasis.ANALYTIC_ADJUSTED)
        for exp, group in puts.groupby("expiration_date", sort=True):
            for _, short in group.sort_values("strike", ascending=False).iterrows():
                longs = group[group.strike.eq(float(short.strike) - raw_width)]
                comparison_short = registry.to_comparison_strike(ticker, day, float(short.strike))
                if longs.empty or (close - comparison_short) / atr < 2.3: continue
                long = longs.iloc[0]; credit = float(short.bid - long.ask)
                if credit > 0:
                    candidates.append((float(short.strike), exp, short, long, credit))
        if not candidates:
            rec["reason_code"] = "NO_VALID_STANDARDIZED_PCS"; rows.append(rec); continue
        short_strike, exp, short, long, credit = candidates[0]
        rec.update({"standardized_pcs_constructable": True, "expiration": exp,
                    "dte": int((pd.Timestamp(exp)-day).days), "short_strike": short_strike,
                    "long_strike": float(long.strike), "width": 5.0, "credit": credit,
                    "liquidity_status": "PASS" if float(short.bid) > 0 and float(long.ask) >= 0 else "FAIL",
                    "credit_status": "PASS", "event_state": "NOT_EVALUATED"})
        if rec["liquidity_status"] != "PASS": rec["reason_code"] = "LIQUIDITY_FAIL"; rows.append(rec); continue
        candidate = {"candidate_id": _identity(ticker, day, exp, short_strike, float(long.strike)),
                    "ticker": ticker, "date": day, "expiration": exp,
                    "short_strike": short_strike, "long_strike": float(long.strike),
                    "initial_credit": credit, "contract_mapping_available": True}
        try:
            q = access.read_quotes(ticker, day, pd.Timestamp(exp), expirations=[exp], strikes=[short_strike, float(long.strike)])
            validate_lifecycle_corporate_action(candidate, registry)
            lifecycle_rows.extend(build_lifecycle_quote_rows(q, candidate))
            rec["executable_pcs"] = True; rec["reason_code"] = "EXECUTABLE_PCS"
        except LifecycleAdapterError as exc:
            rec["reason_code"] = str(exc).split(":")[0]
        rows.append(rec)
    outcome = pd.DataFrame(rows)
    if lifecycle_rows:
        adapter = Stage4ALifecycleReplayAdapter(pd.DataFrame(lifecycle_rows), ReplayPolicy())
        replayed=[]
        for r in outcome[outcome.executable_pcs].to_dict("records"):
            replayed.append({**r, **adapter({"ticker":ticker,"date":r["trade_date"],"expiration":r["expiration"],"short_strike":r["short_strike"],"long_strike":r["long_strike"],"initial_credit":r["credit"],"candidate_id":_identity(ticker,r["trade_date"],r["expiration"],r["short_strike"],r["long_strike"])})})
        outcome = outcome.drop(columns=[c for c in ["pnl","stopped","exit_reason","days_held"] if c in outcome.columns], errors="ignore").merge(pd.DataFrame(replayed), on=["trade_date","ticker","expiration","short_strike","long_strike","credit"], how="left", suffixes=("","_replay"))
    _parquet_safe(outcome).to_parquet(out / "broad_pcs_outcome_map.parquet", index=False)
    _parquet_safe(state_df).to_parquet(out / "pit_feature_ready_calendar.parquet", index=False)
    summary = {"module":"pcs.research.nvda_entry_discovery_v2", "version":V2_VERSION,
               "symbol":ticker, "as_of":end, "status":"COMPLETED", "data_source":"PCS_CANONICAL_DATA",
               "TRAIN_TRADING_DAYS":len(daily), "PIT_FEATURE_READY_DAYS":len(ready),
               "OPTION_DATA_AVAILABLE_DAYS":int(outcome.option_chain_available.sum()),
               "EXECUTABLE_PCS_DATES":int(outcome.executable_pcs.sum()), "final_oos_read":False,
               "validation_read":False, "production_changes":False,
               "reason_codes":["FROM_SCRATCH_V2","NO_OLD_STATE_IMPORTED","NO_ENTRY_GATES","AUTHORITATIVE_LIFECYCLE"]}
    (out / "broad_outcome_map_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
