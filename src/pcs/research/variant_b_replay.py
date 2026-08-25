"""Research-only Variant B option-chain replay.

This module intentionally does not import or alter production eligibility code.
It consumes the existing point-in-time setup context and raw option loader,
then applies the frozen observable option constraints to every spread pair.
Missing lifecycle marks are reported as unavailable rather than synthesized.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from .annualized_metrics import annualized_performance_metrics
import duckdb
from pcs.data.access import PCSDataAccess, DataAccessError
from pcs.data.ticker_registry import get_ticker_state

from pcs.research.credit_stop import (load_entry_chain, load_entry_chain_duckdb_view,
    load_quotes, load_quotes_canonical, load_quotes_canonical_index, load_spread_quotes, load_spread_quotes_duckdb_view,
    valid_entry, valid_exit)
from pcs.research.entry_candidate_universe import (
    FROZEN_CREDIT_WIDTH_MIN, FROZEN_DTE_MAX, FROZEN_DTE_MIN,
    FROZEN_SAFE_STRIKE_ATR, _daily, _atr14, build_historical_setup_context,
    evaluate_intended_pullback_variant,
)
from pcs.research.scheduled_event_calendar import load_calendar
from pcs.data.duckdb_store import connect as connect_duckdb


@dataclass(frozen=True)
class ReplayPolicy:
    version: str = "variant-b-replay-v1"
    safe_strike_atr: float = FROZEN_SAFE_STRIKE_ATR
    dte_min: int = FROZEN_DTE_MIN
    dte_max: int = FROZEN_DTE_MAX
    credit_width_min: float = FROZEN_CREDIT_WIDTH_MIN
    planned_loss_multiple: float = 1.0
    profit_capture_multiple: float = 0.5
    stop_cost_multiple: float = 2.0
    max_quote_days: int = 20
    reject_expiration_crossing: bool = True
    allowed_spread_widths: tuple[float, ...] = (5.0, 10.0, 2.0)
    pre_earnings_exit_days: int | None = None


def _event_reason(calendar: pd.DataFrame, ticker: str, entry: pd.Timestamp,
                  expiry: pd.Timestamp, trading_sessions=None) -> str | None:
    if calendar is None or calendar.empty:
        return "EVENT_CALENDAR_UNAVAILABLE"
    rows = calendar[(calendar.event_type == "EARNINGS") &
                    ((calendar.symbol == ticker) | calendar.symbol.isna())]
    for event in pd.to_datetime(rows.event_date).dt.normalize():
        # Past events cannot create entry blackout or expiration exposure.
        # Without this guard, bdate_range(entry, past_event) is empty and
        # incorrectly satisfies the <=3-day blackout test.
        if event < entry:
            continue
        if entry <= event <= expiry:
            return "EVENT_EARNINGS_CROSSING"
        # Existing EventGate uses business-day distance rather than calendar days.
        sessions = pd.DatetimeIndex(trading_sessions).normalize() if trading_sessions is not None else None
        distance = (int(((sessions > entry) & (sessions <= event)).sum())
                    if sessions is not None else len(pd.bdate_range(entry, event, inclusive="right")))
        if 0 <= distance <= 3:
            return "EVENT_PRE_EARNINGS_BLACKOUT"
    return None


def _load_replay_calendar(path: str | Path) -> pd.DataFrame:
    """Load the canonical calendar and preserve its PIT contract.

    A malformed or legacy export must not be upgraded in memory by guessing
    column names or provenance.  Callers then receive the loader's empty
    unavailable-calendar result (or its validation error) and fail closed.
    """
    out = load_calendar(path)
    out.attrs["historical_pit_required"] = True
    return out


def _spread_candidates(chain: pd.DataFrame, day: pd.Timestamp, close: float,
                       atr: float, setup: dict[str, Any], policy: ReplayPolicy) -> list[dict[str, Any]]:
    puts = chain[chain["Call/Put"].eq("p")].copy()
    puts["DTE"] = (puts["Expiry Date"] - day).dt.days
    out = []
    for expiry, exp in puts[puts.DTE.between(policy.dte_min, policy.dte_max)].groupby("Expiry Date"):
        shorts = exp[(exp.Strike < close) & ((close - exp.Strike) / atr >= policy.safe_strike_atr)]
        for _, short in shorts.iterrows():
            # Research spread-width policy: try one exact listed strike for
            # each approved width, in priority order. Never enumerate all
            # lower strikes or synthesize a missing strike.
            selected_longs = []
            for width in policy.allowed_spread_widths:
                target = float(short.Strike) - float(width)
                matches = exp[np.isclose(exp.Strike.astype(float), target, rtol=0.0, atol=1e-9)]
                if len(matches):
                    selected_longs.append((float(width), matches.iloc[0]))
            for width, long in selected_longs:
                if not (valid_entry(short) and valid_entry(long)):
                    continue
                short_spread = (short["Ask Price"] - short["Bid Price"]) / max((short["Ask Price"] + short["Bid Price"]) / 2, 1e-12)
                long_spread = (long["Ask Price"] - long["Bid Price"]) / max((long["Ask Price"] + long["Bid Price"]) / 2, 1e-12)
                if short["Open Interest"] < 500 or short["Volume"] < 100 or short_spread > .18 or long_spread > .18:
                    continue
                credit = float(short["Bid Price"] - long["Ask Price"])
                width = float(short.Strike - long.Strike)
                ratio = credit / width if width > 0 else 0.0
                if credit <= 0 or ratio < policy.credit_width_min:
                    continue
                max_loss = max(0.0, width - credit) * 100
                planned = min(max_loss, credit * policy.planned_loss_multiple * 100)
                out.append({
                    "date": day, "ticker": setup.get("ticker"), "expiration": pd.Timestamp(expiry),
                    "short_strike": float(short.Strike), "long_strike": float(long.Strike),
                    "dte": int((expiry - day).days), "atr": float(atr),
                    "atr_distance": float((close - short.Strike) / atr),
                    "credit": credit, "spread_width": width, "credit_width_ratio": ratio,
                    "planned_loss": planned, "theoretical_max_loss": max_loss,
                    "short_delta": short.get("Delta"),
                    "short_bid": float(short["Bid Price"]), "short_ask": float(short["Ask Price"]),
                    "long_bid": float(long["Bid Price"]), "long_ask": float(long["Ask Price"]),
                    "short_oi": int(short["Open Interest"]), "short_volume": int(short["Volume"]),
                    "long_oi": int(long["Open Interest"]), "long_volume": int(long["Volume"]),
                    "preferred_dte": 30 <= int((expiry - day).days) <= 40,
                    "trend_state": setup.get("trend_state"), "pullback_state": setup.get("pullback_state"),
                    "support_state": setup.get("support_state"),
                })
    return out


def _replay_lifecycle(candidate: dict[str, Any], quotes: pd.DataFrame,
                      policy: ReplayPolicy) -> dict[str, Any]:
    if quotes.empty:
        return {"status": "UNAVAILABLE", "exit_reason": "INSUFFICIENT_QUOTES"}
    q = quotes[(quotes.Strike.isin([candidate["short_strike"], candidate["long_strike"]])) &
               (quotes["Call/Put"].astype(str).str.lower() == "p") &
               (quotes["Trade Date"] > candidate["date"])].copy()
    marks = []
    for day, rows in q.groupby("Trade Date"):
        short = rows[rows.Strike == candidate["short_strike"]]
        long = rows[rows.Strike == candidate["long_strike"]]
        if len(short) != 1 or len(long) != 1:
            continue
        s, l = short.iloc[0], long.iloc[0]
        if not valid_exit(s, l):
            continue
        debit = float(s["Ask Price"] - l["Bid Price"])
        mid = float((s["Bid Price"] + s["Ask Price"]) / 2 - (l["Bid Price"] + l["Ask Price"]) / 2)
        marks.append((pd.Timestamp(day), debit, mid))
    marks = marks[:policy.max_quote_days]
    if not marks:
        return {"status": "UNAVAILABLE", "exit_reason": "INSUFFICIENT_QUOTES"}
    initial = candidate["credit"]
    profits = [x for x in marks if x[1] <= initial * policy.profit_capture_multiple]
    stops = [x for x in marks if x[1] >= initial * policy.stop_cost_multiple]
    stop = min(stops, default=None, key=lambda x: x[0])
    profit = min(profits, default=None, key=lambda x: x[0])
    if profit and (not stop or profit[0] <= stop[0]):
        exit_mark, reason = profit, "PROFIT_CAPTURE"
    elif stop:
        exit_mark, reason = stop, "STOP"
    else:
        exit_mark = marks[-1]
        if len(marks) < policy.max_quote_days and exit_mark[0] < pd.Timestamp(candidate["expiration"]).normalize():
            return {"status": "RIGHT_CENSORED", "exit_date": exit_mark[0], "exit_reason": "RIGHT_CENSORED",
                    "realized_pnl": None, "premium_capture": None, "mae": None, "mfe": None,
                    "mark_count": len(marks), "right_censored": True, "time_exit": False}
        reason = "TIME_EXIT" if len(marks) >= policy.max_quote_days else "EXPIRATION"
    costs = [x[1] for x in marks]
    pnl = (initial - exit_mark[1]) * 100
    return {"status": "COMPLETE", "exit_date": exit_mark[0], "exit_reason": reason,
            "realized_pnl": pnl, "premium_capture": (initial - exit_mark[1]) / initial if initial else None,
            "mae": max(costs) - initial, "mfe": initial - min(costs),
            "mark_count": len(marks), "stop_triggered": stop is not None,
            "time_exit": reason == "TIME_EXIT"}


def build_targeted_quote_index(symbol: str, requests: list[dict[str, Any]], db_path: str = "data/duckdb/pcs.duckdb", max_quote_days: int = 20) -> tuple[dict[tuple[pd.Timestamp, float], pd.DataFrame], dict[str, Any]]:
    """Load requested contracts through PCSDataAccess, with legacy fallback."""
    if not requests:
        return {}, {"source": "parquet", "partitions_requested": 0, "rows_retained": 0}
    access = PCSDataAccess()
    contracts = {}
    windows = []
    for req in requests:
        expiration = pd.Timestamp(req["expiration"]).normalize()
        for strike_key in ("short_strike", "long_strike"):
            strike = float(req[strike_key])
            contracts[(str(symbol).upper(), expiration, "p", strike)] = None
        # max_quote_days is a quote/trading-day horizon.  Request a bounded
        # calendar superset, then lifecycle marks are limited to the first
        # valid canonical quote days.  Expiration remains the hard bound.
        end = min(expiration, pd.Timestamp(req["start"]).normalize() + pd.Timedelta(days=3 * max_quote_days))
        windows.append((req["start"], end))
    try:
        normalized = sorted({(pd.Timestamp(a).normalize(), pd.Timestamp(b).normalize()) for a, b in windows})
        # Coalesce overlapping windows before the single canonical read.
        merged_windows = []
        for start, end in normalized:
            if merged_windows and start <= merged_windows[-1][1] + pd.Timedelta(days=1):
                merged_windows[-1] = (merged_windows[-1][0], max(merged_windows[-1][1], end))
            else:
                merged_windows.append((start, end))
        frame = access.read_quotes_for_windows(symbol, merged_windows)
        if not frame.empty:
            frame = frame.rename(columns={"trade_date":"Trade Date", "expiration_date":"Expiry Date", "call_put":"Call/Put", "strike":"Strike", "bid":"Bid Price", "ask":"Ask Price", "open_interest":"Open Interest", "volume":"Volume", "delta":"Delta"})
            frame["Trade Date"] = pd.to_datetime(frame["Trade Date"]).dt.normalize()
            frame["Expiry Date"] = pd.to_datetime(frame["Expiry Date"]).dt.normalize()
            wanted = pd.MultiIndex.from_tuples(contracts, names=["symbol", "expiration", "call_put", "strike"])
            actual = pd.MultiIndex.from_frame(pd.DataFrame({"symbol": frame["symbol"].astype(str).str.upper(), "expiration": frame["Expiry Date"], "call_put": frame["Call/Put"].astype(str).str.lower(), "strike": frame["Strike"].astype(float)}))
            frame = frame[actual.isin(wanted)]
            key = ["symbol", "Trade Date", "Expiry Date", "Call/Put", "Strike"]
            if frame.duplicated(key, keep=False).any():
                raise DataAccessError("conflicting or duplicate canonical contract/date quotes")
        quotes = frame if not frame.empty else pd.DataFrame(columns=["symbol", "Trade Date", "Expiry Date", "Call/Put", "Strike", "Bid Price", "Ask Price", "Open Interest", "Volume", "Delta"])
        meta_source = "pcs_data_access"
        partitions = set()
    except (DataAccessError, FileNotFoundError, ValueError) as exc:
        # Canonical replay must fail closed when the active route is absent;
        # legacy monthly storage is reserved for migration/audit tools.
        raise DataAccessError(f"active canonical route unavailable for {symbol}") from exc
    quotes["Trade Date"] = pd.to_datetime(quotes.get("Trade Date"), errors="coerce")
    quotes["Expiry Date"] = pd.to_datetime(quotes.get("Expiry Date"), errors="coerce")
    index = {(pd.Timestamp(exp).normalize(), str(call_put).lower(), float(strike)): group.sort_values("Trade Date").copy() for (exp, call_put, strike), group in quotes.groupby(["Expiry Date", "Call/Put", "Strike"], sort=False)}
    return index, {"source": meta_source, "partitions_requested": len(partitions), "rows_retained": len(quotes), "contracts_indexed": len(index)}


def _replay_lifecycle_batch(candidate: dict[str, Any], quote_index: dict[tuple, pd.DataFrame],
                            policy: ReplayPolicy) -> dict[str, Any]:
    """Replay using cached leg histories; semantics match _replay_lifecycle."""
    identity = pd.Timestamp(candidate["expiration"]).normalize()
    short = quote_index.get((identity, "p", float(candidate["short_strike"])))
    long = quote_index.get((identity, "p", float(candidate["long_strike"])))
    if short is None or long is None:
        return {"status": "UNAVAILABLE", "exit_reason": "INSUFFICIENT_QUOTES"}
    merged = short.merge(long, on="Trade Date", how="outer", suffixes=("_short", "_long"), validate="one_to_one").sort_values("Trade Date")
    marks = []
    missing = 0
    for _, row in merged[merged["Trade Date"] > candidate["date"]].iterrows():
        required = ["Bid Price_short", "Ask Price_short", "Bid Price_long", "Ask Price_long"]
        if any(pd.isna(row.get(field)) for field in required):
            missing += 1
            continue
        if not (row["Ask Price_short"] > 0 and row["Bid Price_long"] >= 0 and
                row["Bid Price_short"] <= row["Ask Price_short"] and
                row["Bid Price_long"] <= row["Ask Price_long"]):
            missing += 1
            continue
        # Conservative executable close estimate: buy back short at ask,
        # sell long at bid. This is identical to the legacy replay formula.
        debit = float(row["Ask Price_short"] - row["Bid Price_long"])
        mid = float((row["Bid Price_short"] + row["Ask Price_short"]) / 2 -
                    (row["Bid Price_long"] + row["Ask Price_long"]) / 2)
        marks.append((pd.Timestamp(row["Trade Date"]), debit, mid))
    if not marks:
        return {"status": "UNAVAILABLE", "exit_reason": "INSUFFICIENT_QUOTES", "missing_mark_count": missing}
    marks = marks[:policy.max_quote_days]
    initial = candidate["credit"]
    profits = [x for x in marks if x[1] <= initial * policy.profit_capture_multiple]
    stops = [x for x in marks if x[1] >= initial * policy.stop_cost_multiple]
    stop = min(stops, default=None, key=lambda x: x[0])
    profit = min(profits, default=None, key=lambda x: x[0])
    forced = None
    if policy.pre_earnings_exit_days is not None and candidate.get("earnings_date") is not None:
        cutoff = pd.Timestamp(candidate["earnings_date"]) - pd.offsets.BDay(policy.pre_earnings_exit_days)
        eligible = [x for x in marks if x[0] <= cutoff]
        if eligible:
            forced = eligible[-1]
    if forced is not None and (not profit or forced[0] < profit[0]) and (not stop or forced[0] < stop[0]):
        exit_mark, reason = forced, "PRE_EARNINGS_EXIT"
    elif profit and (not stop or profit[0] <= stop[0]):
        exit_mark, reason = profit, "PROFIT_CAPTURE"
    elif stop:
        exit_mark, reason = stop, "STOP"
    else:
        exit_mark = marks[-1]
        if len(marks) < policy.max_quote_days and exit_mark[0] < pd.Timestamp(candidate["expiration"]).normalize():
            return {"status": "RIGHT_CENSORED", "exit_date": exit_mark[0], "exit_reason": "RIGHT_CENSORED",
                    "realized_pnl": None, "premium_capture": None, "mae": None, "mfe": None,
                    "mark_count": len(marks), "missing_mark_count": missing,
                    "right_censored": True, "time_exit": False}
        reason = "TIME_EXIT" if len(marks) >= policy.max_quote_days else "EXPIRATION"
    costs = [x[1] for x in marks]
    return {"status": "COMPLETE", "exit_date": exit_mark[0], "exit_reason": reason,
            "realized_pnl": (initial - exit_mark[1]) * 100,
            "premium_capture": (initial - exit_mark[1]) / initial if initial else None,
            "mae": max(costs) - initial, "mfe": initial - min(costs),
            "mark_count": len(marks), "missing_mark_count": missing,
            "stop_triggered": stop is not None and reason == "STOP", "time_exit": reason == "TIME_EXIT",
            "forced_earnings_exit": reason == "PRE_EARNINGS_EXIT",
            "stop_underlying_price": None, "stop_atr": None,
            "stop_distance_atr": None, "stop_strike_itm": None,
            "stop_iv": None, "post_stop_recovery": "UNAVAILABLE"}


def compare_lifecycle_loaders(candidate: dict[str, Any], quotes: pd.DataFrame,
                              quote_index: dict[tuple, pd.DataFrame],
                              policy: ReplayPolicy | None = None) -> dict[str, Any]:
    """Research equivalence check between legacy and batched lifecycle paths."""
    policy = policy or ReplayPolicy()
    old = _replay_lifecycle(candidate, quotes, policy)
    new = _replay_lifecycle_batch(candidate, quote_index, policy)
    fields = ("status", "exit_date", "exit_reason", "realized_pnl", "mae", "mfe", "premium_capture")
    differences = {}
    for field in fields:
        left, right = old.get(field), new.get(field)
        if pd.isna(left) and pd.isna(right):
            continue
        if left != right:
            differences[field] = {"legacy": left, "batch": right}
    return {"equivalent": not differences, "differences": differences, "legacy": old, "batch": new}


def replay_dates(ticker: str, daily_path: str | Path, option_root: str | Path,
                 dates: list[str] | pd.Series, benchmark_path: str | Path,
                 calendar_path: str | Path, baseline_contexts: dict[str, dict[str, Any]] | None = None,
                 benchmark_symbol: str | None = None,
            policy: ReplayPolicy | None = None) -> pd.DataFrame:
    """Replay all A/B candidates for explicit dates; never selects one spread."""
    policy = policy or ReplayPolicy()
    if not benchmark_symbol:
        raise ValueError("benchmark_symbol is required and must match benchmark_path")
    for symbol in (ticker, benchmark_symbol):
        state = get_ticker_state(symbol)
        if state.PCS_RESEARCH_READY != "YES":
            raise DataAccessError(f"ticker readiness blocked replay for {symbol}: {state.PRIMARY_BLOCKER}")
    dates = [value for value in dates if pd.Timestamp(value).year >= 2020]
    access = PCSDataAccess()
    stock = _daily(daily_path, ticker, access); benchmark = _daily(benchmark_path, benchmark_symbol, access)
    stock["atr14"] = _atr14(stock)
    calendar = _load_replay_calendar(calendar_path)
    pending = []
    # One bounded Parquet scan for all entry dates in this run.  The index is
    # immutable and local to the replay invocation; semantics are unchanged.
    entry_index, entry_meta = load_quotes_canonical_index(ticker, min(dates), max(dates)) if dates else ({}, {"scan_count": 0})
    for raw_day in dates:
        day = pd.Timestamp(raw_day).normalize()
        row = stock[stock.date.eq(day)]
        if row.empty or pd.isna(row.iloc[0].atr14):
            continue
        context = (baseline_contexts or {}).get(str(day.date()))
        if context is None:
            context = build_historical_setup_context(stock, benchmark, day, ticker, benchmark_symbol)
        variant = evaluate_intended_pullback_variant(context)
        baseline = context.get("pullback_gate_result")
        a = getattr(baseline, "pullback_gate_result", None)
        b = variant["result"]
        if b != "PASS" and a != "PASS":
            continue
        chain = entry_index.get(day, pd.DataFrame()).copy()
        if chain.empty:
            continue
        close = float(row.iloc[0].close); atr = float(row.iloc[0].atr14)
        setup = {**context, "ticker": ticker}
        for candidate in _spread_candidates(chain, day, close, atr, setup, policy):
            event_reason = _event_reason(calendar, ticker, day, candidate["expiration"], stock.date)
            event_date = next((x for x in pd.to_datetime(calendar.loc[(calendar.event_type == "EARNINGS") & ((calendar.symbol == ticker) | calendar.symbol.isna()), "event_date"]).dt.normalize() if x >= day), None)
            crosses = bool(event_date is not None and day <= event_date <= candidate["expiration"])
            if event_reason == "EVENT_PRE_EARNINGS_BLACKOUT":
                continue
            if event_reason == "EVENT_CALENDAR_UNAVAILABLE":
                continue
            if policy.reject_expiration_crossing and crosses:
                continue
            expiry = candidate["expiration"]
            group = "BASELINE_A" if a == "PASS" and b != "PASS" else "VARIANT_B_ORIGINAL" if a == "PASS" else "VARIANT_B_CONVERTED"
            if group == "VARIANT_B_CONVERTED" and candidate["support_state"] == "weak":
                subgroup = "VARIANT_B_WEAK_SUPPORT"
            elif group == "VARIANT_B_CONVERTED":
                subgroup = "VARIANT_B_MODERATE_SUPPORT"
            else:
                subgroup = group
            days_to_event = len(pd.bdate_range(day, event_date, inclusive="right")) if event_date is not None else None
            pending.append({**candidate, "population": group, "subgroup": subgroup,
                            "baseline_pullback": a, "variant_pullback": b,
                            "event_crosses_earnings": crosses, "earnings_date": event_date,
                            "days_to_earnings": days_to_event,
                            "expected_management_window": policy.max_quote_days})
    requests = [{"start": row["date"], "end": row["date"] + pd.Timedelta(days=3 * policy.max_quote_days),
                 "expiration": row["expiration"], "short_strike": row["short_strike"], "long_strike": row["long_strike"]} for row in pending]
    quote_index, quote_meta = build_targeted_quote_index(ticker, requests, max_quote_days=policy.max_quote_days)
    quote_meta["entry_scan_count"] = entry_meta.get("scan_count", 0)
    quote_meta["entry_rows_returned"] = entry_meta.get("rows_returned", 0)
    records = []
    for row in pending:
        lifecycle = _replay_lifecycle_batch(row, quote_index, policy)
        records.append({**row, "batch_quote_rows": quote_meta.get("rows_retained", 0), **lifecycle})
    return pd.DataFrame(records)


def summarize_replay(frame: pd.DataFrame, by: str | None = None, *, starting_equity: float | None = None,
                     test_start_date=None, test_end_date=None) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    groups = [("ALL", frame)] if by is None else list(frame.groupby(by, dropna=False))
    rows = []
    for key, g in groups:
        complete = g[g.status.eq("COMPLETE")]
        pnl = complete["realized_pnl"].dropna() if "realized_pnl" in complete else pd.Series(dtype=float)
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        rows.append({"group": key, "setup_candidates": len(g), "completed": len(complete),
                     "unavailable": int((g.status != "COMPLETE").sum()),
                     "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
                     "average_pnl": float(pnl.mean()) if len(pnl) else None,
                     "median_pnl": float(pnl.median()) if len(pnl) else None,
                     "expectancy": float(pnl.mean()) if len(pnl) else None,
                     "average_winner": float(wins.mean()) if len(wins) else None,
                     "average_loser": float(losses.mean()) if len(losses) else None,
                     "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() else None,
                     "stop_frequency": float(complete.get("exit_reason", pd.Series(index=complete.index)).eq("STOP").mean()) if len(complete) else None,
                     "time_exit_frequency": float(complete.get("exit_reason", pd.Series(index=complete.index)).eq("TIME_EXIT").mean()) if len(complete) else None,
                     "mae": float(complete["mae"].mean()) if len(complete) and "mae" in complete else None,
                     "mfe": float(complete["mfe"].mean()) if len(complete) and "mfe" in complete else None,
                     "premium_capture": float(complete["premium_capture"].mean()) if len(complete) and "premium_capture" in complete else None,
                     "worst_trade": float(pnl.min()) if len(pnl) else None,
                     **annualized_performance_metrics(g, starting_equity=starting_equity,
                                                      test_start_date=test_start_date,
                                                      test_end_date=test_end_date)})
    return pd.DataFrame(rows)


__all__ = ["ReplayPolicy", "build_targeted_quote_index", "compare_lifecycle_loaders", "replay_dates", "summarize_replay", "annualized_performance_metrics"]
