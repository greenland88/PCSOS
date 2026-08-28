"""Run the fixed-contract, non-IV Covered Call timing study."""
from __future__ import annotations

import json
import time
import argparse
from pathlib import Path
from datetime import date
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import (PriceBasis, PriceBasisError,
                                  assert_comparable_contract,
                                  load_corporate_actions,
                                  transform_frame_to_basis)
from pcs.research.covered_call import CoveredCallResearchConfig, build_sell_timing_features, select_contract
from pcs.research.covered_call_research import replay_prepared_entry_observations, ReplayQuoteProvider
from pcs.research.covered_call_research import _contracts_from_frame


TICKERS = ("QQQ", "SPY", "NVDA", "AMD")
OUT = Path("data/staging/covered_call_stage_a1_non_iv")


def market_frame(access: PCSDataAccess) -> pd.DataFrame:
    frame = pd.read_parquet("data/derived/canonical_pit_market_states.parquet").copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    def unpack(value):
        return json.loads(value) if isinstance(value, str) else (value or {})
    states = frame["market_state"].map(unpack)
    frame["spy_confirmation"] = states.map(lambda x: x.get("breadth_positive"))
    frame["qqq_confirmation"] = states.map(lambda x: x.get("breadth_positive"))
    return frame[["date", "spy_confirmation", "qqq_confirmation"]]


def family_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    trend = (frame["close_vs_sma20"] >= 0) & (frame["close_vs_sma50"] >= 0) & (frame["close_vs_sma200"] >= 0)
    over = frame["distance_to_sma20_atr"] >= 1.0
    recent = frame["distance_from_20d_high"] >= -0.02
    atr = frame["distance_to_sma20_atr"] >= 1.0
    strong = trend & (frame["return_20d"] > 0) & (frame["distance_to_sma20_atr"] >= 1.5)
    return {
        "ALWAYS_SELL": pd.Series(True, index=frame.index),
        "TREND": trend,
        "OVEREXTENSION": over,
        "RECENT_HIGH_RESISTANCE": recent,
        "ATR_EXTENSION": atr,
        "STRONG_UPTREND_NO_SELL": ~strong,
        "TREND_OVEREXTENSION": trend & over,
        "TREND_RECENT_HIGH": trend & recent,
    }


def prepared_from_cache(symbol, entries, provider, daily):
    prices = {str(pd.Timestamp(r.date).date()): float(r.close) for r in daily.itertuples()}
    all_quotes = provider.quotes_by_date()
    chain_by_day = all_quotes
    prepared = []
    for entry in entries:
        observations = []
        entry_day = str(pd.Timestamp(entry["date"]).date())
        expiration_day = str(pd.Timestamp(entry["expiration"]).date())
        lifecycle_days = sorted(day for day in prices if entry_day <= day <= expiration_day)
        for day, contracts in all_quotes.items():
            if entry_day <= day <= expiration_day:
                if not provider.has_quote(symbol, day, entry["expiration"], float(entry["strike"]), "c"):
                    continue
                match = provider.get_quote(symbol, day, entry["expiration"], float(entry["strike"]), "c")
                if match is not None and day in prices:
                    observations.append({"date": day, "underlying_close": prices[day],
                                         "bid": match["bid"], "ask": match["ask"],
                                         "expiration": entry["expiration"],
                                         "chain": contracts})
        quote_by_day = {str(x["date"])[:10]: x for x in observations}
        observations = [{"date": day, "underlying_close": prices[day],
                         **quote_by_day[day]} if day in quote_by_day else
                        {"date": day, "underlying_close": prices[day],
                         "bid": None, "ask": None, "expiration": entry["expiration"],
                         "chain": chain_by_day.get(day, [])}
                        for day in lifecycle_days]
        if observations and entry_day in prices:
            prepared.append({"entry": entry, "observations": observations,
                             "stock_entry_price": prices[entry_day]})
    return prepared


def main(start: str = "2020-01-02", end: str = "2026-08-18", tickers=TICKERS,
         target_dte: int = 30, target_delta: float = .20, families=None,
         selection_method: str = "DELTA",
         target_moneyness: float | None = None,
         target_atr_distance: float | None = None) -> dict:
    access = PCSDataAccess.canonical()
    baseline_config = CoveredCallResearchConfig()
    mkt = market_frame(access)
    corporate_actions = load_corporate_actions()
    all_reports = {}
    for symbol in tickers:
        option_spec = access.resolve_source("options", symbol)
        study_end = min(pd.Timestamp(end), pd.Timestamp(option_spec.last_date))
        daily = build_sell_timing_features(access.read_prices(symbol, start, study_end.date()))
        daily_adjusted = daily.copy()
        # Technical signals may be computed in adjusted space, but option
        # selection/lifecycle must compare absolute values in the option
        # source basis.  The transform scales only absolute price fields;
        # returns and relative distances remain unchanged.
        daily = transform_frame_to_basis(
            daily, symbol=symbol, date_column="date",
            from_basis=PriceBasis.ANALYTIC_ADJUSTED,
            to_basis=PriceBasis.MARKET_RAW, registry=corporate_actions)
        frame = daily.merge(mkt, on="date", how="inner")
        valid = frame.dropna(subset=["close", "atr", "close_vs_sma20", "close_vs_sma50", "close_vs_sma200",
                                    "distance_to_sma20_atr", "distance_from_20d_high", "return_20d",
                                    "spy_confirmation", "qqq_confirmation"]).copy()
        masks = family_masks(valid)
        unique_dates = sorted({day for mask in masks.values() for day in valid.loc[mask, "date"]}, key=str)
        # The exact PIT chain is read once per unique date.  All families use
        # the same frozen contract selector and cannot influence one another.
        selected_by_date = {}
        provider = ReplayQuoteProvider(data_access=access)
        quarter_checkpoints = []
        quote_columns = ["symbol", "trade_date", "expiration_date", "strike", "call_put",
                         "bid", "ask", "delta", "open_interest", "volume"]
        for period in sorted({pd.Timestamp(x).to_period("Q") for x in unique_dates}):
            quarter_dates = [x for x in unique_dates if pd.Timestamp(x).to_period("Q") == period]
            started = time.perf_counter()
            quarter_start = max(period.start_time.date(), pd.Timestamp(option_spec.first_date).date())
            quarter_end = min(period.end_time.date(), study_end.date())
            # Read bounded monthly chunks inside the quarter. The access API
            # returns one DataFrame per call, so a single quarter request can
            # exceed memory on large option universes.
            chunk_starts = pd.date_range(quarter_start, quarter_end, freq="7D")
            for chunk_start_ts in chunk_starts:
                chunk_start = max(chunk_start_ts.date(), quarter_start)
                chunk_end = min((chunk_start_ts + pd.Timedelta(days=6)).date(), quarter_end)
                chunk_dates = [x for x in quarter_dates if chunk_start <= pd.Timestamp(x).date() <= chunk_end]
                option_frame = access.read_quotes_for_windows(
                    symbol, [(chunk_start, chunk_end)], columns=quote_columns)
                provider.canonical_option_reads += 1
                provider.quarter_load_count += 1
                if not option_frame.empty:
                    option_frame["trade_date"] = pd.to_datetime(option_frame["trade_date"]).dt.normalize()
                provider.preload_frame(symbol, option_frame, chain_dates=set(chunk_dates), max_dte=target_dte)
                by_day = {day: group for day, group in option_frame.groupby("trade_date", sort=False)} if not option_frame.empty else {}
                empty = option_frame.iloc[0:0]
                for raw_day in chunk_dates:
                    day_ts = pd.Timestamp(raw_day).normalize()
                    day = day_ts.date().isoformat()
                    day_frame = by_day.get(day_ts, empty)
                    if not day_frame.empty:
                        exp = pd.to_datetime(day_frame["expiration_date"]).dt.normalize()
                        day_frame = day_frame.loc[
                            exp.eq(day_ts + pd.Timedelta(days=target_dte)) &
                            day_frame["call_put"].astype(str).str.lower().isin(["c", "call"])
                        ]
                    chain = _contracts_from_frame(day_frame, symbol)
                    row = valid[valid.date.eq(day_ts)].iloc[-1]
                    chosen = select_contract(chain, config=baseline_config, dte=target_dte, target_delta=target_delta,
                                             selection_method=selection_method,
                                             underlying_price=float(row.close), atr=float(row.atr),
                                             target_moneyness=target_moneyness,
                                             target_atr_distance=target_atr_distance)
                    if chosen is not None:
                        try:
                            assert_comparable_contract(spot=float(row.close), strike=float(chosen.strike),
                                                       price_basis=PriceBasis.MARKET_RAW)
                        except PriceBasisError:
                            continue
                        selected_by_date[day_ts] = {
                            "date": day, "symbol": symbol, "close": float(row.close), "atr": float(row.atr),
                            "expiration": chosen.expiration, "strike": chosen.strike, "bid": chosen.bid,
                            "ask": chosen.ask, "delta": chosen.delta, "dte": chosen.dte,
                            "price_basis": PriceBasis.MARKET_RAW.value,
                            "quote_basis": PriceBasis.MARKET_RAW.value,
                            "split_factor": corporate_actions.adjustment_factor(
                                symbol, day_ts, PriceBasis.MARKET_RAW, PriceBasis.ANALYTIC_ADJUSTED),
                            "actual_otm": (float(chosen.strike) - float(row.close)) / float(row.close),
                            "actual_atr_distance": (float(chosen.strike) - float(row.close)) / float(row.atr),
                            "target_otm": (float(target_moneyness) - 1.0 if selection_method.upper() == "MONEYNESS" and target_moneyness is not None else None),
                            "target_atr": (float(target_atr_distance) if selection_method.upper() == "ATR" and target_atr_distance is not None else None),
                            "spot_raw": float(row.close),
                            "spot_adjusted": float(daily_adjusted.loc[daily_adjusted.date.eq(day_ts), "close"].iloc[-1]),
                            "mid": (float(chosen.bid) + float(chosen.ask)) / 2.0,
                            "contracts": 1,
                            "contract_multiplier": 100,
                            "selection_mode": str(selection_method).upper(),
                            "target_value": (target_moneyness if selection_method.upper() == "MONEYNESS"
                                             else target_atr_distance if selection_method.upper() == "ATR"
                                             else target_delta),
                            "contract_identity": {"symbol": chosen.symbol, "quote_date": chosen.quote_date,
                                                   "expiration": chosen.expiration, "strike": chosen.strike},
                        }
                del option_frame, by_day
            quarter_checkpoints.append({"ticker": symbol, "year": period.year, "quarter": period.quarter,
                                        "status": "PASS", "last_processed_date": str(max(quarter_dates).date()),
                                        "baseline_contracts": sum(pd.Timestamp(x) in selected_by_date for x in quarter_dates),
                                        "elapsed_seconds": round(time.perf_counter() - started, 3)})
        reports = {}
        # Load only the next quarter(s) needed by selected expirations; this
        # supplies cross-quarter lifecycle quotes without a wide-window read.
        needed_quarters = {pd.Timestamp(e["expiration"]).to_period("Q") for e in selected_by_date.values()}
        loaded_quarters = {pd.Period(p, freq="Q") for p in {pd.Timestamp(x).to_period("Q") for x in unique_dates}}
        for period in sorted(needed_quarters - loaded_quarters):
            provider.preload_quarter(symbol, period.year, period.quarter,
                                     start=option_spec.first_date, end=study_end.date())
        for name, mask in masks.items():
            if families and name not in families:
                continue
            dates = sorted(valid.loc[mask, "date"].unique())
            eligible_entries = [selected_by_date[x] for x in dates if x in selected_by_date]
            # One persistent 100-share lot supports at most one short call.
            # Signal dates remain eligible dates; only non-overlapping dates
            # become actual opens for this serial covered-call population.
            entries = []
            capacity_blocked = []
            active_until = None
            for candidate in eligible_entries:
                entry_day = pd.Timestamp(candidate["date"]).date()
                if active_until is not None and entry_day < active_until:
                    capacity_blocked.append({"date": candidate["date"], "reason_code": "ACTIVE_CALL_CAPACITY_BLOCK"})
                    continue
                entries.append(candidate)
                active_until = pd.Timestamp(candidate["expiration"]).date()
            prepared = prepared_from_cache(symbol, entries, provider, daily)
            # Preserve entry-level breach evidence for Stage B audit metrics.
            strike_breached_by_entry = {
                str(item["entry"]["date"]): any(
                    obs.get("underlying_close") is not None
                    and float(obs["underlying_close"]) > float(item["entry"]["strike"])
                    for obs in item.get("observations", [])
                )
                for item in prepared
            }
            replay = replay_prepared_entry_observations(symbol, prepared, unified_lifecycle=True)
            trades = replay.get("trades", replay.get("completed", []))
            family_mtm = 0.0
            for mtm_row in replay.get("lifecycle_audit", []):
                if mtm_row.get("status") == "OPEN_AT_REPLAY_END" and mtm_row.get("final_ask") is not None:
                    mtm_row["lineage_cashflow_to_date"] = float(mtm_row.get("cumulative_credits") or 0) - float(mtm_row.get("cumulative_btc_cost") or 0)
                    mtm_row["final_ask_value"] = float(mtm_row["final_ask"]) * 100
                    mtm_row["unrealized_option_pnl"] = mtm_row["lineage_cashflow_to_date"] - mtm_row["final_ask_value"]
                    family_mtm += mtm_row["unrealized_option_pnl"]
            if name == "ALWAYS_SELL":
                audit_rows = replay.get("lifecycle_audit", [])
                prepared_dates = {str(x["entry"]["date"])[:10] for x in prepared}
                for entry in entries:
                    if entry["date"] not in prepared_dates:
                        audit_rows.append({"entry_date": entry["date"], "expiration": entry["expiration"],
                            "strike": entry["strike"], "underlying_entry": entry["close"],
                            "underlying_expiration": None, "entry_premium": entry["bid"] * 100,
                            "final_action": "QUOTE_MISSING", "status": "QUOTE_MISSING"})
                for row in audit_rows:
                    meta = selected_by_date.get(pd.Timestamp(row["entry_date"]).normalize())
                    if meta:
                        row.update({"spot_raw": meta["spot_raw"], "spot_adjusted": meta["spot_adjusted"],
                                    "split_factor": meta["split_factor"], "strike": meta["strike"],
                                    "bid": meta["bid"], "ask": meta["ask"], "mid": meta["mid"],
                                    "delta": meta["delta"], "price_basis": meta["price_basis"],
                                    "quote_basis": meta["quote_basis"], "selection_mode": meta["selection_mode"],
                                    "target_otm": meta["target_otm"], "actual_otm": meta["actual_otm"],
                                    "target_atr": meta["target_atr"], "actual_atr_distance": meta["actual_atr_distance"],
                                    "contracts": 1, "contract_multiplier": 100})
                    if row["status"] == "COMPLETED":
                        match_trade = next((t for t in trades if t.get("entry_date") == row["entry_date"]), None)
                        if match_trade and match_trade.get("exit_state") == "FORCED_BTC_TO_PROTECT_SHARES":
                            row["status"] = "FORCED_BTC_TO_PROTECT_SHARES"
                        elif match_trade and match_trade.get("exit_state") == "ASSIGNED":
                            row["status"] = "ASSIGNED"
                        else:
                            row["status"] = "BTC_PROFIT" if match_trade and float(match_trade.get("call_realized_pnl", 0) or 0) > 0 else "BTC_LOSS"
                        if row["final_action"] == "EXPIRE_WORTHLESS": row["status"] = "EXPIRED_OTM"
                for row in audit_rows:
                    if row["status"] == "OPEN_AT_REPLAY_END":
                        if row.get("final_ask") is None:
                            row["mtm_status"] = "MTM_QUOTE_UNAVAILABLE"
                        else:
                            row["lineage_cashflow_to_date"] = float(row.get("cumulative_credits") or 0) - float(row.get("cumulative_btc_cost") or 0)
                            row["final_ask_value"] = float(row["final_ask"]) * 100
                            row["unrealized_option_pnl"] = row["lineage_cashflow_to_date"] - row["final_ask_value"]
                            row["mtm_status"] = "MTM_COMPLETE"
                reports.setdefault("_lifecycle_audit", audit_rows)
            pnl = [float(x.get("call_realized_pnl", x.get("option_only_pnl", 0)) or 0) for x in trades]
            positive = [x for x in pnl if x > 0]
            negative = [x for x in pnl if x < 0]
            running = 0.0
            peak = 0.0
            max_drawdown = 0.0
            for value in pnl:
                running += value
                peak = max(peak, running)
                max_drawdown = min(max_drawdown, running - peak)
            breach_count = sum(strike_breached_by_entry.get(str(x["date"]), False) for x in entries)
            yearly = {}
            for trade, value in zip(trades, pnl):
                year = str(trade.get("entry_date", trade.get("date", "UNKNOWN")))[:4]
                yearly[year] = yearly.get(year, 0.0) + value
            reports[name] = {
                "sell_dates": len(dates), "candidate_dates": len(eligible_entries),
                "eligible_sell_dates": len(dates), "capacity_blocked_dates": len(capacity_blocked),
                "actual_opened_calls": len(entries),
                "completed_calls": len(trades), "option_only_pnl": sum(pnl),
                "unrealized_option_pnl": family_mtm,
                "total_option_economic_pnl": sum(pnl) + family_mtm,
                "expectancy": (sum(pnl) / len(pnl)) if pnl else None,
                "profit_factor": (sum(positive) / abs(sum(negative))) if negative else None,
                "gross_premium": sum(float(x.get("call_premium", 0) or 0) for x in trades),
                "premium_collected": sum(float(x.get("call_premium", 0) or 0) for x in trades),
                "btc_costs": sum(float(x.get("btc_cost", 0) or 0) for x in trades),
                "fees": sum(float(x.get("fees", 0) or 0) for x in trades),
                "normal_btc_cost": sum(float(x.get("normal_btc_cost", 0) or 0) for x in trades),
                "roll_close_cost": sum(float(x.get("roll_close_cost", 0) or 0) for x in trades),
                "roll_open_credit": sum(float(x.get("roll_open_credit", 0) or 0) for x in trades),
                "forced_btc_cost": sum(float(x.get("forced_btc_cost", 0) or 0) for x in trades),
                "forced_btc_loss": sum(float(x.get("forced_btc_loss", 0) or 0) for x in trades),
                "roll_count": sum(int(x.get("roll_count", 0) or 0) for x in trades) +
                              (sum(int(x.get("roll_count", 0) or 0) for x in replay.get("lifecycle_audit", []))
                               if name == "ALWAYS_SELL" else 0),
                "rolled_open_count": sum(1 for x in replay.get("lifecycle_audit", [])
                                          if x.get("final_action") == "ROLLED") if name == "ALWAYS_SELL" else 0,
                "forced_btc_count": sum(x.get("exit_state") == "FORCED_BTC_TO_PROTECT_SHARES" for x in trades),
                "true_open_at_replay_end": sum(1 for x in replay.get("lifecycle_audit", [])
                                                if x.get("status") == "OPEN_AT_REPLAY_END") if name == "ALWAYS_SELL" else 0,
                "assignment_count": sum(x.get("exit_state") == "ASSIGNED" for x in trades),
                "assignment_rate": sum(x.get("exit_state") == "ASSIGNED" for x in trades) / len(trades) if trades else None,
                "upside_drag": sum(float(x.get("upside_sacrificed", 0) or 0) for x in trades),
                "yearly_option_only_pnl": yearly,
                "positive_year_count": sum(v > 0 for v in yearly.values()),
                "worst_year": min(yearly.items(), key=lambda x: x[1]) if yearly else None,
                "max_drawdown": max_drawdown if pnl else None,
                "strike_breach_count": breach_count,
                "strike_breach_rate": breach_count / len(entries) if entries else None,
            }
        audit = reports.pop("_lifecycle_audit", [])
        counts = {}
        for row in audit: counts[row["status"]] = counts.get(row["status"], 0) + 1
        all_reports[symbol] = {"feature_rows": len(valid), "families": reports,
                               "selected_contracts": list(selected_by_date.values()),
                               "price_basis": PriceBasis.MARKET_RAW.value,
                               "quote_basis": PriceBasis.MARKET_RAW.value,
                               "always_sell_lifecycle_audit": audit,
                               "always_sell_lifecycle_counts": counts,
                               "quarter_checkpoints": quarter_checkpoints,
                               "instrumentation": provider.instrumentation(),
                               "baseline_contract": {"dte": target_dte, "delta": target_delta, "quote": "PIT_BID", "liquidity": True,
                                                      "price_basis": PriceBasis.MARKET_RAW.value}}
    OUT.mkdir(parents=True, exist_ok=True)
    result = {"module": "pcs.research.covered_call_stage_a1_non_iv", "status": "DESCRIPTIVE_ONLY",
              "data_source": "PCS_CANONICAL_DATA", "final_oos_read": False,
              "contract_selection_frozen": True, "iv_included": False, "tickers": all_reports}
    (OUT / "stage_a1_summary.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-08-18")
    parser.add_argument("--symbols", nargs="+", default=list(TICKERS))
    parser.add_argument("--dte", type=int, default=30)
    parser.add_argument("--delta", type=float, default=.20)
    parser.add_argument("--families", nargs="+", default=None)
    args = parser.parse_args()
    print(json.dumps(main(args.start, args.end, tuple(args.symbols), args.dte, args.delta, args.families), indent=2, default=str))
