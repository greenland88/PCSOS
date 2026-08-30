"""Execute the governed NVDA Covered Call A2 timing study (research only)."""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.research.covered_call import build_sell_timing_features
from pcs.research.covered_call_research import (
    _contracts_from_frame, _read_quotes_chunked, replay_selected_entries,
)
from pcs.research.covered_call_timing import (
    FrozenContractNeighborhood, TimingFamily, WaitState,
    persist_a2_artifacts, run_covered_call_timing_neighborhood,
)

ROOT = Path(__file__).resolve().parents[1]
NVDA_CC_MIN_VALID_DATE = pd.Timestamp("2024-06-10")
FAMILIES = list(TimingFamily)
WAITS = list(WaitState)


def prefilter_candidate_frame(frame: pd.DataFrame, *, enforce_delta: bool = True) -> pd.DataFrame:
    """Apply the governed central-cell candidate filter before indexing."""
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], format="ISO8601")
    if (out["trade_date"] < NVDA_CC_MIN_VALID_DATE).any():
        raise ValueError("NVDA_CC_PRE_SPLIT_OPTION_QUOTE_REJECTED")
    out["expiration_date"] = pd.to_datetime(out["expiration_date"], format="ISO8601")
    out["dte"] = (out["expiration_date"] - out["trade_date"]).dt.days
    delta = pd.to_numeric(out["delta"], errors="coerce").abs()
    mask = (
        out["call_put"].astype(str).str.upper().isin(["C", "CALL"])
        & out["dte"].between(14, 35)
        & pd.to_numeric(out["bid"], errors="coerce").gt(0)
        & pd.to_numeric(out["ask"], errors="coerce").ge(pd.to_numeric(out["bid"], errors="coerce"))
    )
    if enforce_delta:
        mask &= delta.between(0.15, 0.20)
    for column in ("tradable", "executable"):
        if column in out.columns:
            mask &= out[column].fillna(False).astype(bool)
    if "price_basis" in out.columns:
        mask &= out["price_basis"].eq("MARKET_RAW")
    return out.loc[mask].copy()


def select_indexed_contract(candidates, *, spot: float, atr: float, neighborhood):
    """Select from one already-indexed PIT date; never reads or filters data."""
    from pcs.research.covered_call_timing import select_frozen_neighborhood_contract
    items = [{"symbol": c.symbol, "quote_date": c.quote_date,
              "strike": c.strike, "bid": c.bid, "ask": c.ask,
              "delta": c.delta, "dte": c.dte,
              "actual_atr_distance": (c.strike - float(spot)) / float(atr),
              "contract_identity": {"symbol": c.symbol, "quote_date": c.quote_date,
                                     "expiration": c.expiration, "strike": c.strike}}
             for c in candidates]
    return select_frozen_neighborhood_contract(items, spot=float(spot), neighborhood=neighborhood)


def _indexed_items(candidates, *, spot: float, atr: float):
    """Convert one PIT day's already-filtered contracts for selection."""
    return [{"symbol": c.symbol, "quote_date": c.quote_date,
             "strike": c.strike, "bid": c.bid, "ask": c.ask,
             "delta": c.delta, "dte": c.dte,
             "actual_atr_distance": (c.strike - float(spot)) / float(atr),
             "price_basis": "MARKET_RAW",
             "contract_identity": {"symbol": c.symbol, "quote_date": c.quote_date,
                                    "expiration": c.expiration, "strike": c.strike}}
            for c in candidates]

def main() -> None:
    access = PCSDataAccess.canonical()
    source = access.resolve_source("options", "NVDA")
    start = NVDA_CC_MIN_VALID_DATE
    end = min(pd.Timestamp("2026-08-18"), pd.Timestamp(source.last_date))
    daily = build_sell_timing_features(access.read_prices("NVDA", start.date(), end.date()))
    market = pd.read_parquet(ROOT / "data/derived/canonical_pit_market_states.parquet")
    market["date"] = pd.to_datetime(market["date"]).dt.normalize()
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    daily = daily[daily["date"].between(start, end)].copy()
    market = market[market["date"].between(start, end)].copy()
    full_neighborhood = FrozenContractNeighborhood(14, 35, (0.15, 0.20), (0.075, 0.10, 0.15), (2.5, 3.0, 4.0))
    scope = os.environ.get("A2_SCOPE", "FAST").upper()
    surface = scope == "OTM_SURFACE"
    dte_surface = scope == "DTE_SURFACE"
    dte_target_otm = float(os.environ.get("DTE_TARGET_OTM", "0.10"))
    robustness = os.environ.get("A2_SCOPE", "FAST").upper() == "ROBUSTNESS"
    neighborhood = (FrozenContractNeighborhood(14, 35, (0.01, 0.99),
                    (dte_target_otm,), (3.0,)) if dte_surface else
                    FrozenContractNeighborhood(14, 35, (0.15, 0.20),
                    (0.05, 0.075, 0.10, 0.125, 0.15), (3.0,)) if surface else
                    full_neighborhood if robustness else
                    FrozenContractNeighborhood(14, 35, (0.15, 0.20), (0.10,), (3.0,)))
    families = ([TimingFamily.ALWAYS_SELL] if surface or dte_surface else
                [TimingFamily.ALWAYS_SELL, TimingFamily.STRONG_RALLY] if robustness else FAMILIES)
    chain_cache = {}
    indexed_items_cache = {}
    perf = {"raw_option_rows": 0, "prefiltered_candidate_rows": 0,
            "indexed_dates": 0, "contract_selection_calls": 0,
            "contract_selection_cache_hits": 0}
    phase = {}
    management_quote_cache = {}
    chain_columns = ["symbol", "trade_date", "expiration_date", "strike", "call_put",
                     "bid", "ask", "delta", "open_interest", "volume"]

    def chains(symbol, day):
        key = str(pd.Timestamp(day).date())
        return chain_cache.get(key, [])

    def lifecycle(signals, policy):
        entries, active_until = [], None
        contracts_by_entry = {}
        for signal in sorted(signals, key=lambda x: str(x["date"])):
            contract = signal["contract"]
            day = pd.Timestamp(signal["date"]).date()
            expiry = pd.Timestamp(contract["contract_identity"].get("expiration")).date()
            if active_until is not None and day < active_until:
                continue
            entries.append({"date": str(day), "symbol": "NVDA", "close": contract["spot"],
                            "expiration": str(expiry), "strike": contract["strike"],
                            "bid": contract["bid"], "ask": contract["ask"],
                            "delta": contract["delta"], "dte": contract["dte"],
                            "actual_otm": contract["actual_otm"],
                            "actual_atr_distance": contract["actual_atr_distance"],
                            "selection_mode": contract["selection_mode"],
                            "price_basis": "MARKET_RAW"})
            # replay_selected_entries emits the canonical lifecycle identity but
            # does not echo the selection metadata.  One serial position means
            # at most one selected entry per date, so date is the stable join key.
            contracts_by_entry[str(day)] = dict(contract)
            active_until = expiry
        quote_provider = None
        if entries:
            first_day = min(pd.Timestamp(x["date"]).normalize() for x in entries)
            last_day = max(pd.Timestamp(x["expiration"]).normalize() for x in entries)
            cache_key = (str(first_day.date()), str(last_day.date()))
            if cache_key not in management_quote_cache:
                frame = _read_quotes_chunked(access, "NVDA", [(first_day, last_day)], chain_columns)
                by_date = {}
                if not frame.empty:
                    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="ISO8601")
                    frame["expiration_date"] = pd.to_datetime(frame["expiration_date"], format="ISO8601")
                    frame["dte"] = (frame["expiration_date"] - frame["trade_date"]).dt.days
                    frame = frame[
                        frame["call_put"].astype(str).str.upper().isin(["C", "CALL"])
                        & frame["dte"].between(1, 90)
                    ]
                    for day, day_frame in frame.groupby(frame["trade_date"].dt.normalize()):
                        by_date[str(pd.Timestamp(day).date())] = _contracts_from_frame(day_frame, "NVDA")
                management_quote_cache[cache_key] = by_date
            class _CachedQuoteProvider:
                def __init__(self, data): self._data = data
                def quotes_by_date(self): return self._data
            quote_provider = _CachedQuoteProvider(management_quote_cache[cache_key])
        try:
            replay = replay_selected_entries("NVDA", entries, data_access=access,
                                             quote_provider=quote_provider, unified_lifecycle=True)
        except ValueError as exc:
            # A hard lifecycle constraint in one cell must not abort the
            # research population. Preserve it as an auditable exclusion.
            return [{"symbol": "NVDA", "entry_date": entries[0]["date"],
                     "status": "HARD_CONSTRAINT_CONFLICT",
                     "economic_status": "EXCLUDED_FROM_NORMAL_PNL",
                     "reason_codes": [str(exc)], "net_call_only_pnl": 0.0,
                     "contract": contracts_by_entry.get(entries[0]["date"])}]
        rows = []
        for trade in replay.get("trades", []):
            entry_date = str(trade.get("entry_date", trade.get("date", "")))[:10]
            contract = contracts_by_entry.get(entry_date)
            rows.append({**trade, "net_call_only_pnl": trade.get("call_realized_pnl", 0),
                         "gross_premium": trade.get("call_premium", 0),
                         "BTC_cost": trade.get("btc_cost", 0),
                         "forced_btc": trade.get("exit_state") == "FORCED_BTC_TO_PROTECT_SHARES",
                         "expiration_itm": trade.get("exit_state") == "ASSIGNED",
                         "contract": contract or {"dte": trade.get("dte"), "delta": trade.get("delta"),
                                                     "actual_otm": trade.get("actual_otm"),
                                                     "actual_atr_distance": trade.get("actual_atr_distance")}})
        return rows

    rows = __import__("pcs.research.covered_call_timing", fromlist=["build_pit_timing_rows"]).build_pit_timing_rows(daily, market)
    row_dates = sorted({pd.Timestamp(x["date"]).normalize() for x in rows})
    load_started = time.perf_counter()
    for offset in range(0, len(row_dates), 128):
        batch = row_dates[offset:offset + 128]
        frame = _read_quotes_chunked(access, "NVDA", [(day, day) for day in batch], chain_columns)
        if frame.empty:
            continue
        perf["raw_option_rows"] += len(frame)
        frame = prefilter_candidate_frame(frame, enforce_delta=not (dte_surface or surface))
        perf["prefiltered_candidate_rows"] += len(frame)
        for day, day_frame in frame.groupby(frame["trade_date"]):
            key = str(pd.Timestamp(day).date())
            contracts = _contracts_from_frame(day_frame, "NVDA")
            chain_cache[key] = contracts
            indexed_items_cache[key] = contracts
    phase["options_load_seconds"] = time.perf_counter() - load_started
    perf["indexed_dates"] = len(chain_cache)
    # Select each independent frozen OTM/ATR cell from canonical PIT chains.
    from pcs.research.covered_call_timing import select_frozen_neighborhood_contract
    selected_contract_cache = {}
    selection_seconds = 0.0
    def selector(row, frozen):
        nonlocal selection_seconds
        call_started = time.perf_counter()
        spot, atr = row.get("close"), row.get("atr")
        try:
            if spot is None or atr is None: return None
            date_key = str(pd.Timestamp(row.get("date")).date())
            cache_key = (date_key, "MONEYNESS", float(frozen.otm_values[0]), float(frozen.atr_values[0]))
            perf["contract_selection_calls"] += 1
            if cache_key in selected_contract_cache:
                perf["contract_selection_cache_hits"] += 1
                return dict(selected_contract_cache[cache_key])
            items = _indexed_items(indexed_items_cache.get(date_key, []), spot=float(spot), atr=float(atr))
            selected = select_frozen_neighborhood_contract(items, spot=float(spot), neighborhood=frozen)
            if selected is not None:
                selected_contract_cache[cache_key] = dict(selected)
            return selected
        finally:
            selection_seconds += time.perf_counter() - call_started
    # Fast first pass: answer the timing question on the frozen center cell.
    # The surrounding cells are a second-stage robustness check only when the
    # center cell shows a potentially useful WAIT effect.
    if dte_surface:
        # DTE isolation: select nearest expiry in a fixed +/-3-day tolerance.
        dte_results = []
        for target_dte in (14, 21, 30):
            dte_neighborhood = FrozenContractNeighborhood(target_dte - 3, target_dte + 3,
                                                          (0.01, 0.99), (dte_target_otm,), (3.0,))
            def dte_selector(row, frozen, target=target_dte):
                spot, atr = row.get("close"), row.get("atr")
                if spot is None or atr is None: return None
                key = str(pd.Timestamp(row["date"]).date())
                candidates = _indexed_items(indexed_items_cache.get(key, []), spot=float(spot), atr=float(atr))
                from pcs.research.covered_call_timing import select_frozen_neighborhood_contract
                return select_frozen_neighborhood_contract(
                    candidates, spot=float(spot), neighborhood=frozen,
                    target_otm=dte_target_otm, target_dte=target, dte_tolerance=3,
                    enforce_delta=False)
            dte_result = run_covered_call_timing_neighborhood(
                "NVDA", {"start": str(start.date()), "end": str(end.date()),
                         "mode": "NEW_ENTRY", "data_scope": "POST_SPLIT_ONLY",
                         "canonical_quote_basis_status": "VALIDATED_POST_SPLIT_ONLY"},
                dte_neighborhood, families, WAITS,
                {"unified_lifecycle": True, "price_basis": "MARKET_RAW"}, daily_rows=rows,
                select_contract=dte_selector, run_lifecycle=lifecycle,
                checkpoint_dir=ROOT / f"data/staging/covered_call_a2_nvda_dte_surface/checkpoints/{target_dte}")
            dte_results.append(dte_result)
        result = dte_results[0]
        result["dte_surface_results"] = [x["timing_families"]["ALWAYS_SELL"] for x in dte_results]
        dte_out = ROOT / "data/staging/covered_call_a2_nvda_dte_surface"
        dte_out.mkdir(parents=True, exist_ok=True)
        (dte_out / "dte_surface_results.json").write_text(
            json.dumps(result["dte_surface_results"], indent=2, default=str), encoding="utf-8")
    else:
        result = run_covered_call_timing_neighborhood(
                "NVDA", {"start": str(start.date()), "end": str(end.date()),
                         "mode": "NEW_ENTRY", "data_scope": "POST_SPLIT_ONLY",
                         "canonical_quote_basis_status": "VALIDATED_POST_SPLIT_ONLY"},
        neighborhood, families, WAITS,
        {"unified_lifecycle": True, "price_basis": "MARKET_RAW"}, daily_rows=rows,
        select_contract=selector, run_lifecycle=lifecycle,
        checkpoint_dir=ROOT / ("data/staging/covered_call_a2_nvda_robustness/checkpoints" if robustness else "data/staging/covered_call_a2_nvda/checkpoints"))
    result["research_id"] = "covered_call_nvda_a2_timing"
    result["execution_mode"] = ("ALWAYS_SELL_OTM_SURFACE" if surface else
                                 "ROBUSTNESS_TWO_FAMILY" if robustness else
                                 "FAST_CENTER_CELL_FIRST_PASS")
    result["robustness_status"] = "COMPLETED_TWO_FAMILY_NEIGHBORHOOD" if robustness else "PENDING_NEIGHBORHOOD_CONFIRMATION"
    phase["contract_selection_seconds"] = selection_seconds
    result["performance"] = {**phase, **perf,
                              "contract_selection_cache_hit_rate": (
                                  perf["contract_selection_cache_hits"] / perf["contract_selection_calls"]
                                  if perf["contract_selection_calls"] else 0.0)}
    result["status"] = "COMPLETED_RESEARCH_ONLY"
    out = ROOT / ("data/staging/covered_call_a2_nvda_dte_surface" if dte_surface else
                  "data/staging/covered_call_a2_nvda_otm_surface" if surface else
                  "data/staging/covered_call_a2_nvda_robustness" if robustness else
                  "data/staging/covered_call_a2_nvda")
    out.mkdir(parents=True, exist_ok=True)
    paths = persist_a2_artifacts(result, out)
    (out / "run_summary.json").write_text(json.dumps({"status": result["status"], "artifacts": paths,
        "families": list(result["timing_families"]), "chain_dates": len(chain_cache)}, indent=2, default=str))
    print(json.dumps({"status": result["status"], "families": len(families),
                      "chain_dates": len(chain_cache), "artifacts": paths}, indent=2))

if __name__ == "__main__":
    main()
