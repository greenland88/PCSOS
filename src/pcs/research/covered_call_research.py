"""Public orchestration API for covered-call research.

The adapter boundary deliberately accepts a ``symbol`` and canonical rows;
ticker-specific data access remains in PCSDataAccess and is not duplicated.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping
from uuid import uuid4
import json
import pandas as pd
import hashlib
from pathlib import Path

from .covered_call import CoveredCallResearchConfig, aggregate_metrics, replay_covered_call
from .research_framework import ResearchSpec, ResearchMode, load_spec, validate_population_routing
from pcs.data.access import PCSDataAccess
from .covered_call import (CoveredCallContract, CoveredCallPosition, CoveredCallState,
                           CoveredCallDailyEngine)
from .covered_call import select_contract, sell_call_timing_signal


def _read_quotes_chunked(access: PCSDataAccess, symbol: str,
                         windows: list[tuple[Any, Any]], columns: list[str],
                         chunk_size: int = 128) -> pd.DataFrame:
    """Read only requested windows in bounded chunks through PCSDataAccess."""
    frames = []
    for start in range(0, len(windows), chunk_size):
        pending = [windows[start:start + chunk_size]]
        while pending:
            current = pending.pop()
            try:
                frame = access.read_quotes_for_windows(symbol, current, columns=columns)
            except (ValueError, FileNotFoundError):
                continue
            except Exception as exc:
                # PCSDataAccess rejects ambiguous keys at the read boundary.
                # Split only that quality failure so clean dates remain
                # usable; a single conflicting date is fail-closed.
                if "ambiguous option quote keys" not in str(exc).lower():
                    raise
                if len(current) > 1:
                    midpoint = len(current) // 2
                    pending.extend((current[:midpoint], current[midpoint:]))
                continue
            if not frame.empty:
                frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)


def prepare_selected_entry_observations(symbol: str, entries: Iterable[Mapping[str, Any]], *,
                                        data_access: PCSDataAccess | None = None) -> list[dict[str, Any]]:
    """Materialize one PIT-safe quote/price snapshot per selected entry."""
    access = data_access or PCSDataAccess.canonical()
    entries = list(entries)
    if not entries:
        return []
    price_frame = access.read_prices(symbol, min(pd.Timestamp(e["date"]) for e in entries),
                                     max(pd.Timestamp(e["expiration"]) for e in entries))
    prices = {str(pd.Timestamp(r.date).date()): float(r.close) for r in price_frame.itertuples()}
    grouped = {}
    for entry in entries:
        grouped.setdefault(pd.Timestamp(entry["date"]).to_period("Q"), []).append(
            (entry["date"], entry["expiration"]))
    quote_cache = {}
    columns = ["symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask"]
    for key, windows in grouped.items():
        quote_cache[key] = _read_quotes_chunked(access, symbol, windows, columns)
    prepared = []
    for entry in entries:
        start, end = entry["date"], entry["expiration"]
        price_by_date = {k: v for k, v in prices.items()
                         if str(pd.Timestamp(start).date()) <= k <= str(pd.Timestamp(end).date())}
        quotes = quote_cache.get(pd.Timestamp(start).to_period("Q"), pd.DataFrame(columns=columns))
        quotes = quotes[(quotes.expiration_date == pd.Timestamp(end).date()) &
                        (quotes.strike == float(entry["strike"]))].copy()
        if not quotes.empty:
            quotes["trade_date"] = pd.to_datetime(quotes.trade_date).dt.normalize()
        observations = [{"date": str(q.trade_date.date()),
                         "underlying_close": price_by_date[str(q.trade_date.date())],
                         "bid": float(q.bid), "ask": float(q.ask), "expiration": end}
                        for q in quotes[quotes.call_put.astype(str).str.lower().isin({"c", "call"})].itertuples()
                        if str(q.trade_date.date()) in price_by_date]
        if observations and str(pd.Timestamp(start).date()) in price_by_date:
            prepared.append({"entry": entry, "observations": observations,
                             "stock_entry_price": price_by_date[str(pd.Timestamp(start).date())]})
    return prepared


def replay_prepared_entry_observations(symbol: str, prepared: Iterable[Mapping[str, Any]], *,
                                       profit_capture: float = .60,
                                       minimum_holding_days: int = 0,
                                       remaining_dte_condition: int | None = None,
                                       unified_lifecycle: bool = False) -> dict[str, Any]:
    """Replay prepared observations without another canonical I/O pass."""
    if unified_lifecycle:
        trades = []
        for item in prepared:
            entry = item["entry"]
            entry_day = str(pd.Timestamp(entry["date"]).date())
            contract = CoveredCallContract(str(symbol).upper(), entry_day, entry["expiration"],
                                           float(entry["strike"]), float(entry["bid"]), float(entry["ask"]),
                                           float(entry.get("delta") or 0), dte=int(entry["dte"]))
            daily = [{"date": entry_day, "underlying_price": item["stock_entry_price"],
                      "new_entry": True, "entry_contract": contract}]
            quotes = {}
            for obs in item["observations"]:
                day = str(obs["date"])[:10]
                daily.append({"date": day, "underlying_price": float(obs["underlying_close"])})
                quotes.setdefault(day, []).append(CoveredCallContract(
                    str(symbol).upper(), day, obs["expiration"], float(entry["strike"]),
                    float(obs["bid"]), float(obs["ask"]), dte=(date.fromisoformat(obs["expiration"][:10]) - date.fromisoformat(day)).days))
            engine = CoveredCallDailyEngine(str(symbol), profit_capture=profit_capture,
                                             minimum_holding_days=minimum_holding_days,
                                             remaining_dte_condition=remaining_dte_condition)
            replay = engine.run(daily, quotes_by_date=quotes)
            for episode in replay["completed_episodes"]:
                stock_pnl = ((float(episode.close_underlying_price) - episode.stock_entry_price) * episode.shares
                             if episode.close_underlying_price is not None else None)
                trades.append({"symbol": episode.symbol, "entry_date": episode.opened_date,
                               "exit_date": episode.close_date, "holding_days":
                               (date.fromisoformat(episode.close_date[:10]) - date.fromisoformat(episode.opened_date[:10])).days,
                               "roll_count": len(episode.roll_history), "roll_credits": episode.cumulative_roll_credits,
                               "combined_pnl": episode.final_pnl, "stock_pnl": stock_pnl,
                               "call_premium": episode.cumulative_premium_received,
                               "call_realized_pnl": episode.realized_cashflow - (stock_pnl or 0.0),
                               "exit_state": "BUY_TO_CLOSE"})
            for conflict in replay["conflicts"]:
                episode = next(e for e in replay["episodes"] if e.episode_id == conflict["episode_id"])
                trades.append({"symbol": episode.symbol, "entry_date": episode.opened_date,
                               "exit_date": None, "roll_count": len(episode.roll_history),
                               "roll_credits": episode.cumulative_roll_credits,
                               "exit_state": "HARD_CONSTRAINT_CONFLICT", "status": "HARD_CONSTRAINT_CONFLICT"})
        return {"module": "pcs.research.covered_call_research", "version": "2.0",
                "symbol": str(symbol).upper(), "status": "COMPLETED" if trades else "NO_COMPLETED_TRADES",
                "data_source": "PCS_CANONICAL_DATA", "unified_lifecycle": True,
                "trades": trades, "metrics": aggregate_metrics(trades),
                "final_oos_read": False,
                "reason_codes": ["UNIFIED_DAILY_ENGINE", "CONTRACT_VARIANT_FROZEN_ENTRIES"]}
    rows = []
    for item in prepared:
        entry = item["entry"]
        observations = item["observations"]
        position = CoveredCallPosition(str(symbol).upper())
        position.open(float(item["stock_entry_price"]), CoveredCallContract(
            str(symbol).upper(), str(pd.Timestamp(entry["date"]).date()), entry["expiration"],
            float(entry["strike"]), float(entry["bid"]), float(entry["ask"]),
            float(entry.get("delta") or 0), dte=int(entry["dte"])))
        try:
            replay = replay_covered_call(position, observations,
                profit_capture=profit_capture, minimum_holding_days=minimum_holding_days,
                remaining_dte_condition=remaining_dte_condition)
        except ValueError:
            continue
        replay.update({"strike": float(entry["strike"]), "dte_at_entry": int(entry["dte"]),
                       "entry_delta": float(entry.get("delta") or 0),
                       "entry_premium": float(entry["bid"] + entry["ask"]) / 2 * 100})
        if "combined_pnl" in replay:
            buy_hold = (float(observations[-1]["underlying_close"]) - position.stock_entry_price) * 100
            replay.update({"buy_and_hold_pnl": buy_hold,
                           "excess_return_vs_buy_and_hold": replay["combined_pnl"] - buy_hold,
                           "upside_sacrificed": max(buy_hold - replay["combined_pnl"], 0.0)})
        else:
            replay.update({"buy_and_hold_pnl": None, "excess_return_vs_buy_and_hold": None,
                           "upside_sacrificed": None})
        rows.append(replay)
    return {"module": "pcs.research.covered_call_research", "version": "1.0",
            "symbol": str(symbol).upper(), "status": "COMPLETED" if rows else "NO_COMPLETED_TRADES",
            "data_source": "PCS_CANONICAL_DATA", "trades": rows,
            "metrics": aggregate_metrics(rows), "final_oos_read": False,
            "reason_codes": ["PREPARED_CANONICAL_OBSERVATIONS", "H2_NO_LOSS_CLOSE",
                             "H4_REVIEW_ENFORCED"]}


def run_covered_call_research(symbol: str, *, trades: Iterable[Mapping[str, Any]] = (),
                              config: CoveredCallResearchConfig | None = None,
                              as_of: date | str | None = None) -> dict[str, Any]:
    """Return a standard, ticker-agnostic covered-call research envelope.

    ``trades`` must already be produced by a canonical PIT-safe replay adapter;
    this function only aggregates economic outcomes and never reads raw files.
    """
    ticker = str(symbol).strip().upper()
    if not ticker or not ticker.isalnum():
        raise ValueError("INVALID_SYMBOL")
    rows = list(trades)
    return {
        "module": "pcs.research.covered_call_research", "version": "1.0",
        "symbol": ticker, "as_of": str(as_of or date.today()),
        "status": "COMPLETED" if rows else "NO_TRADES",
        "action": "HOLD", "data_timestamp": datetime.now(timezone.utc).isoformat(),
        "calculation_version": "covered_call_economic_v1", "run_id": str(uuid4()),
        "request_id": str(uuid4()), "data_source": "PCS_CANONICAL_DATA",
        "config": asdict(config or CoveredCallResearchConfig()),
        "metrics": aggregate_metrics(rows),
        "reason_codes": ["TICKER_AGNOSTIC", "BUY_AND_HOLD_BENCHMARK", "PIT_SAFE_INPUT_REQUIRED",
                         "RESEARCH_ONLY", "PRODUCTION_WRITE_BLOCKED"],
    }


def run_covered_call_spec(spec: ResearchSpec, *, trades: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Run the covered-call aggregation behind a validated NEW_ENTRY spec."""
    validate_population_routing(spec)
    if spec.research_mode not in {ResearchMode.NEW_ENTRY, ResearchMode.CONTRACT_VARIANT}:
        raise ValueError("COVERED_CALL_RESEARCH_REQUIRES_NEW_ENTRY_OR_CONTRACT_VARIANT")
    if str(spec.rules.get("strategy", "")).upper() != "COVERED_CALL":
        raise ValueError("COVERED_CALL_STRATEGY_RULE_REQUIRED")
    return run_covered_call_research(spec.ticker, trades=trades, config=CoveredCallResearchConfig(**{
        k: tuple(tuple(x) for x in v) if k.endswith("_buckets") else v
        for k, v in spec.rules.get("covered_call_config", {}).items()
    }))


def run_covered_call_spec_file(path: str, *, trades: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    return run_covered_call_spec(load_spec(path), trades=trades)


def read_pit_call_chain(symbol: str, trade_date: Any, *, data_access: PCSDataAccess | None = None) -> list[CoveredCallContract]:
    """Read and normalize one canonical call chain through PCSDataAccess."""
    access = data_access or PCSDataAccess()
    frame = access.read_option_chain(str(symbol).upper(), trade_date)
    required = {"symbol", "trade_date", "expiration_date", "strike", "bid", "ask"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError("CANONICAL_CALL_CHAIN_SCHEMA_MISSING:" + ",".join(sorted(missing)))
    calls = frame[frame.call_put.astype(str).str.lower().isin({"c", "call"})].copy()
    result = []
    for row in calls.itertuples(index=False):
        dte = (datetime.fromisoformat(str(row.expiration_date)[:10]).date() -
               datetime.fromisoformat(str(row.trade_date)[:10]).date()).days
        result.append(CoveredCallContract(
            symbol=str(row.symbol).upper(), quote_date=str(row.trade_date)[:10],
            expiration=str(row.expiration_date)[:10], strike=float(row.strike),
            bid=float(row.bid), ask=float(row.ask),
            delta=float(getattr(row, "delta", 0.0)) if getattr(row, "delta", None) is not None else None,
            open_interest=int(getattr(row, "open_interest", 0)) if getattr(row, "open_interest", None) is not None else None,
            volume=int(getattr(row, "volume", getattr(row, "option_volume", 0))) if getattr(row, "volume", getattr(row, "option_volume", None)) is not None else None,
            underlying_price=float(getattr(row, "underlying_price", 0)) if getattr(row, "underlying_price", None) is not None else None,
            dte=dte))
    return result


def _contracts_from_frame(frame, symbol: str) -> list[CoveredCallContract]:
    calls = frame[frame.call_put.astype(str).str.lower().isin({"c", "call"})].copy()
    result = []
    for row in calls.itertuples(index=False):
        exp = pd.Timestamp(row.expiration_date).date(); day = pd.Timestamp(row.trade_date).date()
        result.append(CoveredCallContract(symbol=str(row.symbol).upper(), quote_date=str(day),
            expiration=str(exp), strike=float(row.strike), bid=float(row.bid), ask=float(row.ask),
            delta=float(getattr(row, "delta", 0.0)) if getattr(row, "delta", None) is not None else None,
            open_interest=int(getattr(row, "open_interest", 0)) if getattr(row, "open_interest", None) is not None else None,
            volume=int(getattr(row, "volume", 0)) if getattr(row, "volume", None) is not None else None,
            dte=(exp - day).days))
    return result


def replay_expiry_or_close(position: CoveredCallPosition, *, stock_exit_price: float,
                           expiration: bool = False, buy_to_close_price: float | None = None) -> dict[str, Any]:
    """Close a position through the shared covered-call lifecycle and report economics."""
    terminal = CoveredCallState.EXPIRE_WORTHLESS if expiration else CoveredCallState.BUY_TO_CLOSE
    position.close(terminal, stock_price=stock_exit_price, buy_to_close_price=buy_to_close_price)
    return {"symbol": position.symbol, "exit_state": terminal.value,
            **position.economic_result(stock_exit_price)}


def discover_and_select_entries(symbol: str, daily, market, *, data_access: PCSDataAccess | None = None,
                                config: CoveredCallResearchConfig | None = None,
                                target_delta: float = .30, dte: int = 43,
                                selection_method: str = "DELTA",
                                target_moneyness: float | None = None,
                                target_atr_distance: float | None = None) -> dict[str, Any]:
    """Discover PIT sell-call dates, then select contracts on those dates.

    ``daily`` and ``market`` are canonical, already-PIT-safe feature frames.
    No future row is consulted while producing a signal or selecting a quote.
    """
    import pandas as pd
    cfg = config or CoveredCallResearchConfig()
    if "date" not in daily.columns or "date" not in market.columns:
        raise ValueError("PIT_FEATURE_DATE_COLUMN_REQUIRED")
    stock = daily.copy(); mkt = market.copy()
    if "atr" not in stock.columns and "atr14" in stock.columns:
        stock = stock.rename(columns={"atr14": "atr"})
    if "market_state" in mkt.columns:
        def unpack(value):
            if isinstance(value, str):
                try: return json.loads(value)
                except json.JSONDecodeError: return {}
            return value if isinstance(value, Mapping) else {}
        states = mkt.market_state.map(unpack).apply(pd.Series)
        mkt = pd.concat([mkt.drop(columns=["market_state"]), states], axis=1)
        if "breadth_positive" in mkt.columns:
            mkt["spy_confirmation"] = mkt["breadth_positive"]
            mkt["qqq_confirmation"] = mkt["breadth_positive"]
    stock["date"] = pd.to_datetime(stock.date).dt.normalize(); mkt["date"] = pd.to_datetime(mkt.date).dt.normalize()
    joined = stock.merge(mkt, on="date", how="left", suffixes=("", "_market")).sort_values("date")
    signals = []
    for row in joined.to_dict("records"):
        signal = sell_call_timing_signal(stock={**row, "symbol": symbol}, market=row, config=cfg)
        if signal["action"] == "OPEN":
            signals.append({"date": row["date"], "symbol": symbol.upper(),
                            "close": row.get("close"), "atr": row.get("atr"), **signal})
    selected = []
    quote_columns = ["symbol", "trade_date", "expiration_date", "strike", "call_put",
                     "bid", "ask", "delta", "open_interest", "volume"]
    if signals and data_access is not None and hasattr(data_access, "read_quotes_for_windows"):
        # Keep only one bounded quote batch alive.  This is important for
        # long-history index tickers whose option chain is much larger than
        # the selected signal population.
        for offset in range(0, len(signals), 128):
            batch = signals[offset:offset + 128]
            dates = [pd.Timestamp(x["date"]).normalize() for x in batch]
            bulk = _read_quotes_chunked(data_access, symbol, [(day, day) for day in dates], quote_columns)
            if not bulk.empty:
                bulk["trade_date"] = pd.to_datetime(bulk.trade_date).dt.normalize()
            for candidate in batch:
                chain = _contracts_from_frame(
                    bulk[bulk.trade_date.eq(pd.Timestamp(candidate["date"]).normalize())], symbol)
                chosen = select_contract(chain, config=cfg, dte=dte, target_delta=target_delta,
                                         selection_method=selection_method,
                                         underlying_price=float(candidate.get("close")) if candidate.get("close") is not None else None,
                                         atr=float(candidate.get("atr")) if candidate.get("atr") is not None else None,
                                         target_moneyness=target_moneyness,
                                         target_atr_distance=target_atr_distance)
                if chosen is not None:
                    selected.append({**candidate, "expiration": chosen.expiration, "strike": chosen.strike,
                                     "bid": chosen.bid, "ask": chosen.ask, "delta": chosen.delta,
                                     "dte": chosen.dte, "contract_identity": {
                                         "symbol": chosen.symbol, "quote_date": chosen.quote_date,
                                         "expiration": chosen.expiration, "strike": chosen.strike}})
            del bulk
    else:
        for candidate in signals:
            chain = read_pit_call_chain(symbol, candidate["date"], data_access=data_access)
            chosen = select_contract(chain, config=cfg, dte=dte, target_delta=target_delta,
                                     selection_method=selection_method,
                                     underlying_price=float(candidate.get("close")) if candidate.get("close") is not None else None,
                                     atr=float(candidate.get("atr")) if candidate.get("atr") is not None else None,
                                     target_moneyness=target_moneyness,
                                     target_atr_distance=target_atr_distance)
            if chosen is not None:
                selected.append({**candidate, "expiration": chosen.expiration, "strike": chosen.strike,
                                 "bid": chosen.bid, "ask": chosen.ask, "delta": chosen.delta,
                                 "dte": chosen.dte, "contract_identity": {
                                     "symbol": chosen.symbol, "quote_date": chosen.quote_date,
                                     "expiration": chosen.expiration, "strike": chosen.strike}})
    return {"module": "pcs.research.covered_call_research", "version": "1.0",
            "symbol": str(symbol).upper(), "status": "COMPLETED" if selected else "NO_CONTRACTS",
            "data_source": "PCS_CANONICAL_DATA", "signal_execution": "PIT_SAFE",
            "funnel": {"ALL_TRADING_DAYS": len(joined), "FEATURE_READY_DAYS": len(joined),
                       "SIGNAL_DATES": len(signals), "CONTRACT_AVAILABLE_DATES": len(selected),
                       "LIQUIDITY_ELIGIBLE_DATES": len(selected)},
            "entries": selected, "final_oos_read": False,
            "reason_codes": ["FULL_TICKER_DAILY_CALENDAR", "SIGNAL_BEFORE_CONTRACT_SELECTION",
                             "PCSDataAccess_CANONICAL_OPTIONS", "EXACT_CONTRACT_IDENTITY"]}


def prepare_entry_signal_chains(symbol: str, daily, market, *,
                                data_access: PCSDataAccess | None = None,
                                config: CoveredCallResearchConfig | None = None,
                                dte: int = 37) -> dict[str, Any]:
    """Discover PIT signals and read each signal-date call chain once."""
    cfg = config or CoveredCallResearchConfig()
    stock = daily.copy(); mkt = market.copy()
    if "atr" not in stock.columns and "atr14" in stock.columns:
        stock = stock.rename(columns={"atr14": "atr"})
    if "market_state" in mkt.columns:
        def unpack(value):
            if isinstance(value, str):
                try: return json.loads(value)
                except json.JSONDecodeError: return {}
            return value if isinstance(value, Mapping) else {}
        states = mkt.market_state.map(unpack).apply(pd.Series)
        mkt = pd.concat([mkt.drop(columns=["market_state"]), states], axis=1)
        if "breadth_positive" in mkt.columns:
            mkt["spy_confirmation"] = mkt["breadth_positive"]
            mkt["qqq_confirmation"] = mkt["breadth_positive"]
    stock["date"] = pd.to_datetime(stock.date).dt.normalize(); mkt["date"] = pd.to_datetime(mkt.date).dt.normalize()
    joined = stock.merge(mkt, on="date", how="left", suffixes=("", "_market")).sort_values("date")
    signals = []
    for row in joined.to_dict("records"):
        signal = sell_call_timing_signal(stock={**row, "symbol": symbol}, market=row, config=cfg)
        if signal["action"] == "OPEN":
            signals.append({"date": row["date"], "symbol": symbol.upper(), "close": row.get("close"),
                            "atr": row.get("atr"), **signal})
    access = data_access or PCSDataAccess.canonical()
    chains = {}
    columns = ["symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask", "delta", "open_interest", "volume"]
    for offset in range(0, len(signals), 128):
        batch = signals[offset:offset + 128]
        dates = [pd.Timestamp(x["date"]).normalize() for x in batch]
        frame = _read_quotes_chunked(access, symbol, [(day, day) for day in dates], columns)
        if not frame.empty:
            frame["trade_date"] = pd.to_datetime(frame.trade_date).dt.normalize()
        for signal in batch:
            day = pd.Timestamp(signal["date"]).normalize()
            chains[day] = _contracts_from_frame(frame[frame.trade_date.eq(day)], symbol)
    return {"symbol": symbol.upper(), "signals": signals, "chains": chains,
            "funnel": {"ALL_TRADING_DAYS": len(joined), "FEATURE_READY_DAYS": len(joined),
                       "SIGNAL_DATES": len(signals)},
            "data_source": "PCS_CANONICAL_DATA", "final_oos_read": False,
            "reason_codes": ["FULL_TICKER_DAILY_CALENDAR", "PIT_SIGNAL_BEFORE_VARIANT_SELECTION",
                             "CANONICAL_CALL_CHAIN_SNAPSHOT"]}


def replay_selected_entries(symbol: str, entries: Iterable[Mapping[str, Any]], *,
                            data_access: PCSDataAccess | None = None,
                            profit_capture: float = .60,
                            minimum_holding_days: int = 0,
                            remaining_dte_condition: int | None = None,
                            unified_lifecycle: bool = False) -> dict[str, Any]:
    """Replay selected entries with canonical quotes and return standard metrics."""
    access = data_access or PCSDataAccess.canonical()
    rows = []
    entries = list(entries)
    if unified_lifecycle:
        # Materialize the same canonical observations once, then process all
        # entry dates through one book.  This is intentionally a separate
        # branch so legacy descriptive reports remain reproducible.
        prepared = prepare_selected_entry_observations(symbol, entries, data_access=access)
        engine = CoveredCallDailyEngine(symbol, profit_capture=profit_capture)
        daily_rows: dict[str, dict[str, Any]] = {}
        quotes_by_date: dict[str, list[CoveredCallContract]] = {}
        # The selected-entry observations contain only the current contract.
        # Fetch the bounded management window once so the selector can see
        # later expirations and strikes at roll dates.
        if prepared and hasattr(access, "read_quotes_for_windows"):
            first_day = min(pd.Timestamp(x["entry"]["date"]).normalize() for x in prepared)
            last_day = max(pd.Timestamp(x["entry"]["expiration"]).normalize() for x in prepared)
            chain = _read_quotes_chunked(
                access, symbol, [(first_day, last_day)],
                ["symbol", "trade_date", "expiration_date", "strike", "call_put",
                 "bid", "ask", "delta", "open_interest", "volume"])
            if not chain.empty:
                chain["trade_date"] = pd.to_datetime(chain.trade_date)
                for day, frame in chain.groupby(chain.trade_date.dt.normalize()):
                    quotes_by_date[str(pd.Timestamp(day).date())] = _contracts_from_frame(frame, symbol)
        for item in prepared:
            entry = item["entry"]
            contract = CoveredCallContract(str(symbol).upper(), str(pd.Timestamp(entry["date"]).date()),
                                           entry["expiration"], float(entry["strike"]),
                                           float(entry["bid"]), float(entry["ask"]),
                                           float(entry.get("delta") or 0), dte=int(entry["dte"]))
            entry_day = str(pd.Timestamp(entry["date"]).date())
            daily_rows.setdefault(entry_day, {"date": entry_day,
                                               "underlying_price": item["stock_entry_price"]}).update(
                                                   new_entry=True, entry_contract=contract)
            for obs in item["observations"]:
                day = str(obs["date"])[:10]
                daily_rows.setdefault(day, {"date": day, "underlying_price": obs["underlying_close"]})
                if day not in quotes_by_date:
                    quotes_by_date[day] = []
                if not any(q.expiration == obs["expiration"] and q.strike == float(entry["strike"])
                           for q in quotes_by_date[day]):
                    quotes_by_date[day].append(CoveredCallContract(
                        str(symbol).upper(), day, obs["expiration"], float(entry["strike"]),
                        float(obs["bid"]), float(obs["ask"]),
                        dte=(date.fromisoformat(obs["expiration"][:10]) - date.fromisoformat(day)).days))
        replay = engine.run(daily_rows.values(), quotes_by_date=quotes_by_date)
        completed = []
        for episode in replay["completed_episodes"]:
            holding_days = (date.fromisoformat(str(episode.close_date)[:10]) -
                            date.fromisoformat(str(episode.opened_date)[:10])).days
            total_pnl = episode.final_pnl
            stock_pnl = ((float(episode.close_underlying_price) - episode.stock_entry_price) * episode.shares
                         if episode.close_underlying_price is not None else None)
            completed.append({"symbol": episode.symbol, "entry_date": episode.opened_date,
                              "exit_date": episode.close_date, "episode_id": episode.episode_id,
                              "holding_days": holding_days,
                              "roll_count": len(episode.roll_history),
                              "roll_credits": episode.cumulative_roll_credits,
                              "combined_pnl": total_pnl, "stock_pnl": stock_pnl,
                              "call_premium": episode.cumulative_premium_received,
                              "call_realized_pnl": episode.realized_cashflow - (stock_pnl or 0.0),
                              "exit_state": "BUY_TO_CLOSE"})
        conflicted_ids = {x["episode_id"] for x in replay.get("conflicts", [])}
        for episode in replay["episodes"]:
            if episode.episode_id in conflicted_ids:
                completed.append({"symbol": episode.symbol, "entry_date": episode.opened_date,
                                  "exit_date": None, "episode_id": episode.episode_id,
                                  "roll_count": len(episode.roll_history),
                                  "roll_credits": episode.cumulative_roll_credits,
                                  "exit_state": "HARD_CONSTRAINT_CONFLICT",
                                  "status": "HARD_CONSTRAINT_CONFLICT",
                                  "economic_status": "EXCLUDED_FROM_NORMAL_PNL",
                                  "reason_codes": ["H1_NO_ASSIGNMENT",
                                                    "LIFECYCLE_QUOTE_UNAVAILABLE_AT_EXPIRY"]})
        return {"module": "pcs.research.covered_call_research", "version": "2.0",
                "symbol": str(symbol).upper(), "status": "COMPLETED" if completed else "NO_COMPLETED_TRADES",
                "data_source": "PCS_CANONICAL_DATA", "unified_lifecycle": True,
                "trades": completed, "actions": replay["actions"],
                "metrics": aggregate_metrics(completed) | {"capacity_rejections": replay["metrics"]["capacity_rejections"]},
                "final_oos_read": False,
                "reason_codes": ["UNIFIED_DAILY_ENGINE", "SINGLE_POSITION_BOOK", "CROSS_YEAR_LIFECYCLE"]}
    price_by_date_global = {}
    if entries:
        try:
            price_frame = access.read_prices(
                symbol, min(pd.Timestamp(e["date"]) for e in entries),
                max(pd.Timestamp(e["expiration"]) for e in entries))
            price_by_date_global = {str(pd.Timestamp(r.date).date()): float(r.close)
                                    for r in price_frame.itertuples()}
        except (ValueError, FileNotFoundError):
            price_by_date_global = {}
    # Quote reads are the dominant cost for long histories.  Group windows by
    # calendar quarter and issue one bounded PCSDataAccess read per quarter;
    # filtering to the exact expiration/strike below preserves identity and
    # PIT semantics while avoiding repeated Parquet/schema scans.
    quote_cache = {}
    if entries and hasattr(access, "read_quotes_for_windows"):
        grouped = {}
        for entry in entries:
            key = pd.Timestamp(entry["date"]).to_period("Q")
            grouped.setdefault(key, []).append((entry["date"], entry["expiration"]))
        for key, windows in grouped.items():
            quote_cache[key] = _read_quotes_chunked(
                access, symbol, windows,
                ["symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask"])
    for entry in entries:
        start, end = entry["date"], entry["expiration"]
        if price_by_date_global:
            price_by_date = {k: v for k, v in price_by_date_global.items()
                             if str(pd.Timestamp(start).date()) <= k <= str(pd.Timestamp(end).date())}
        else:
            prices = access.read_prices(symbol, start, end)
            price_by_date = {str(pd.Timestamp(r.date).date()): float(r.close) for r in prices.itertuples()}
        key = pd.Timestamp(start).to_period("Q")
        quotes = quote_cache.get(key)
        if quotes is None:
            try:
                quotes = access.read_quotes_for_windows(
                    symbol, [(start, end)],
                    columns=["symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask"])
            except (ValueError, FileNotFoundError):
                continue
        quotes = quotes[(quotes.expiration_date == pd.Timestamp(end).date()) &
                        (quotes.strike == float(entry["strike"]))]
        quotes["trade_date"] = pd.to_datetime(quotes.trade_date).dt.normalize()
        observations = []
        for q in quotes[quotes.call_put.astype(str).str.lower().isin({"c", "call"})].itertuples():
            key = str(q.trade_date.date())
            if key in price_by_date:
                observations.append({"date": key, "underlying_close": price_by_date[key],
                                     "bid": float(q.bid), "ask": float(q.ask), "expiration": end})
        if not observations: continue
        position = CoveredCallPosition(symbol.upper())
        position.open(float(price_by_date[str(pd.Timestamp(start).date())]), CoveredCallContract(
            symbol.upper(), str(pd.Timestamp(start).date()), end, float(entry["strike"]),
            float(entry["bid"]), float(entry["ask"]), float(entry["delta"]), dte=int(entry["dte"])))
        try:
            replay = replay_covered_call(
                position, observations, profit_capture=profit_capture,
                minimum_holding_days=minimum_holding_days,
                remaining_dte_condition=remaining_dte_condition)
            replay.update({"strike": float(entry["strike"]), "dte_at_entry": int(entry["dte"]),
                           "entry_delta": float(entry["delta"]), "entry_premium": float(entry["bid"] + entry["ask"]) / 2 * 100})
            rows.append(replay)
        except ValueError as exc:
            # Missing terminal lifecycle observations are a data-quality
            # outcome, not a synthetic exit or an inferred P&L.
            continue
        if "combined_pnl" in rows[-1]:
            rows[-1].update({"buy_and_hold_pnl": (float(observations[-1]["underlying_close"]) - position.stock_entry_price) * 100})
            rows[-1]["excess_return_vs_buy_and_hold"] = rows[-1]["combined_pnl"] - rows[-1]["buy_and_hold_pnl"]
            rows[-1]["upside_sacrificed"] = max(rows[-1]["buy_and_hold_pnl"] - rows[-1]["combined_pnl"], 0.0)
        else:
            rows[-1].update({"economic_status": "EXCLUDED_FROM_NORMAL_PNL", "buy_and_hold_pnl": None,
                             "excess_return_vs_buy_and_hold": None, "upside_sacrificed": None})
    frame = pd.DataFrame(rows)
    yearly = []
    if not frame.empty:
        frame["year"] = pd.to_datetime(frame.entry_date).dt.year
        for year, group in frame.groupby("year"):
            yearly.append({"year": int(year), **aggregate_metrics(group.to_dict("records"))})
    counts = frame.year.value_counts().to_dict() if not frame.empty else {}
    stability = {"years": yearly, "year_count": len(yearly),
                 "positive_years": sum(float(x.get("combined_pnl", 0)) > 0 for x in yearly),
                 "leave_one_year_out": [{"excluded_year": int(y),
                    **aggregate_metrics(frame[frame.year != y].to_dict("records"))} for y in counts]}
    concentration = {"largest_year_trade_share": max(counts.values()) / len(frame) if len(frame) else None,
                     "largest_year_pnl_share": (max((abs(x["combined_pnl"]) for x in yearly), default=0) /
                                                 sum(abs(x["combined_pnl"]) for x in yearly)) if yearly and sum(abs(x["combined_pnl"]) for x in yearly) else None}
    return {"module": "pcs.research.covered_call_research", "version": "1.0",
            "symbol": symbol.upper(), "status": "COMPLETED" if rows else "NO_COMPLETED_TRADES",
            "data_source": "PCS_CANONICAL_DATA", "trades": rows, "metrics": aggregate_metrics(rows),
            "yearly_results": yearly, "parameter_stability": stability,
            "episode_concentration": concentration,
            "final_oos_read": False, "reason_codes": ["CANONICAL_DAILY_PRICES", "CANONICAL_CALL_QUOTES",
                                                         "PIT_ENTRY_DATES", "LIFECYCLE_REPLAYED"]}


def analyze_constraint_failures(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return descriptive failure partitions without changing any rule."""
    trades = list(report.get("trades", []))
    conflicts = [row for row in trades if row.get("status") == "HARD_CONSTRAINT_CONFLICT" or
                 row.get("exit_state") == "HARD_CONSTRAINT_CONFLICT"]
    return {"symbol": report.get("symbol"), "total_episodes": len(trades),
            "conflict_count": len(conflicts),
            "constraint_failure_rate": len(conflicts) / len(trades) if trades else None,
            "conflicts_by_dte": pd.Series([r.get("dte_at_entry") for r in conflicts]).value_counts().to_dict(),
            "conflicts_by_strike": pd.Series([r.get("strike") for r in conflicts]).value_counts().to_dict(),
            "episodes": conflicts, "reason_codes": ["DESCRIPTIVE_FAILURE_ANALYSIS",
                                                      "HARD_CONSTRAINTS_UNCHANGED", "NO_PNL_TUNING"]}


def run_profit_close_parameter_grid(symbol: str, entries: Iterable[Mapping[str, Any]], *,
                                    data_access: PCSDataAccess | None = None,
                                    profit_captures: Iterable[float] = (.40, .50, .60, .65, .70, .75, .80, .85, .90),
                                    minimum_holding_days: Iterable[int] = (3, 5, 10, 15),
                                    remaining_dte_conditions: Iterable[int] = (21, 14, 10, 7)) -> dict[str, Any]:
    """Run the governed H2 close grid over frozen entry dates.

    This is a CONTRACT_VARIANT-style management study: entry dates and
    selected contracts are frozen, while only lifecycle close conditions vary.
    Each cell is an independent canonical replay and retains conflicts rather
    than converting them into P&L.
    """
    frozen = list(entries)
    prepared = prepare_selected_entry_observations(symbol, frozen, data_access=data_access)
    cells = []
    for capture in profit_captures:
        for minimum in minimum_holding_days:
            for remaining in remaining_dte_conditions:
                replay = replay_prepared_entry_observations(
                    symbol, prepared,
                    profit_capture=float(capture),
                    minimum_holding_days=int(minimum),
                    remaining_dte_condition=int(remaining),
                    unified_lifecycle=True)
                cells.append({"profit_capture": float(capture),
                              "minimum_holding_days": int(minimum),
                              "remaining_dte_condition": int(remaining),
                              "metrics": replay.get("metrics", {}),
                              "status": replay.get("status"),
                              "reason_codes": ["CONTRACT_VARIANT_FROZEN_ENTRIES", "UNIFIED_DAILY_ENGINE",
                                               "H2_NO_LOSS_CLOSE", "H4_REVIEW_ENFORCED"]})
    return {"module": "pcs.research.covered_call_profit_close_grid", "version": "1.0",
            "symbol": str(symbol).upper(), "status": "COMPLETED" if cells else "NO_CELLS", "cells": cells,
            "unified_lifecycle": True,
            "entry_count": len(frozen), "final_oos_read": False,
            "production_changes_allowed": False,
            "reason_codes": ["CONTRACT_VARIANT", "PIT_SAFE_QUOTES", "UNIFIED_DAILY_ENGINE",
                             "NO_AUTOMATIC_PROMOTION"]}


def build_parameter_surface(report: Mapping[str, Any], *,
                            config: CoveredCallResearchConfig | None = None) -> dict[str, Any]:
    """Return an auditable observed-vs-missing parameter surface.

    This helper never fills missing cells with zero P&L.  A cell is
    ``OBSERVED`` only when the canonical replay actually contains trades in
    that bucket; otherwise it remains ``NOT_RUN`` so downstream selection
    cannot mistake an untested region for a losing region.
    """
    cfg = config or CoveredCallResearchConfig()
    trades = list(report.get("trades", []))
    cells = []
    for low, high in cfg.dte_buckets:
        subset = [r for r in trades if r.get("dte_at_entry") is not None and
                  low <= int(r["dte_at_entry"]) < high]
        cells.append({"dimension": "DTE", "bucket": [low, high],
                      "status": "OBSERVED" if subset else "NOT_RUN",
                      "trades": len(subset),
                      "metrics": aggregate_metrics(subset) if subset else None,
                      "reason_codes": (["CANONICAL_REPLAY_OBSERVED"] if subset else
                                       ["PARAMETER_CELL_NOT_EXECUTED", "NO_IMPLICIT_ZERO_FILL"])})
    for target in cfg.target_deltas:
        subset = [r for r in trades if r.get("entry_delta") is not None and
                  abs(float(r["entry_delta"]) - target) <= 0.025]
        cells.append({"dimension": "DELTA", "target": target,
                      "status": "OBSERVED" if subset else "NOT_RUN",
                      "trades": len(subset),
                      "metrics": aggregate_metrics(subset) if subset else None,
                      "reason_codes": (["CANONICAL_REPLAY_OBSERVED"] if subset else
                                       ["PARAMETER_CELL_NOT_EXECUTED", "NO_IMPLICIT_ZERO_FILL"])})
    return {"module": "pcs.research.covered_call_parameter_surface", "version": "1.0",
            "symbol": str(report.get("symbol", "")).upper(), "cells": cells,
            "final_oos_read": False, "production_changes_allowed": False,
            "reason_codes": ["OBSERVED_CELLS_ONLY", "PIT_SAFE_INPUT_REQUIRED",
                             "NO_PARAMETER_PROMOTION"]}


def build_transfer_matrix(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = []
    for report in reports:
        metrics = report.get("metrics", {})
        rows.append({"symbol": str(report.get("symbol", "")).upper(),
                     "trades": int(metrics.get("trades", 0) or 0),
                     "combined_pnl": float(metrics.get("combined_pnl", 0) or 0),
                     # aggregate_metrics exposes the normalized name; accept
                     # the lifecycle report alias as well so transfer analysis
                     # cannot silently turn valid overlay evidence into zero.
                     "excess_return": float(metrics.get(
                         "excess_return", metrics.get("excess_return_vs_buy_and_hold", 0)
                     ) or 0)})
    positive = sum(row["excess_return"] > 0 for row in rows)
    classification = ("UNIVERSAL" if len(rows) >= 3 and positive / len(rows) >= .67 else
                      "ARCHETYPE_SPECIFIC" if positive >= 2 else
                      "TICKER_SPECIFIC" if positive == 1 else "NO_EDGE")
    return {"module": "pcs.research.covered_call_research", "version": "1.0",
            "artifact": "covered_call_transfer_matrix", "classification": classification,
            "ticker_count": len(rows), "rows": rows,
            "reason_codes": ["STANDARDIZED_REPORT_INPUT", "NO_TICKER_NAME_RULES",
                             "RESEARCH_ONLY", "NO_AUTOMATIC_PROMOTION"]}


def validate_covered_call_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the stable report envelope before it can be persisted/read."""
    required = {"module", "version", "symbol", "as_of", "status", "data_timestamp",
                "calculation_version", "run_id", "request_id", "data_source", "metrics", "reason_codes"}
    missing = sorted(required - set(report))
    if missing:
        raise ValueError("COVERED_CALL_REPORT_SCHEMA_MISSING:" + ",".join(missing))
    if report["data_source"] != "PCS_CANONICAL_DATA":
        raise ValueError("COVERED_CALL_REPORT_NON_CANONICAL_SOURCE")
    if not isinstance(report["metrics"], Mapping) or not isinstance(report["reason_codes"], list):
        raise ValueError("COVERED_CALL_REPORT_SCHEMA_INVALID_TYPES")
    return {"valid": True, "symbol": str(report["symbol"]).upper(),
            "calculation_version": report["calculation_version"],
            "required_fields": sorted(required), "reason_codes": ["REPORT_SCHEMA_VALIDATED"]}


def build_covered_call_manifest(*, report: Mapping[str, Any], spec_path: str,
                                feature_path: str, market_path: str,
                                daily_manifest_path: str, options_manifest_path: str) -> dict[str, Any]:
    """Build a reproducibility manifest; never marks incomplete identity CURRENT."""
    validate_covered_call_report(report)
    paths = [spec_path, feature_path, market_path, daily_manifest_path, options_manifest_path]
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        return {"current": False, "status": "INCOMPLETE", "missing_paths": missing,
                "reason_codes": ["MANIFEST_INPUT_MISSING"]}
    def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    files = {str(Path(p)): sha(p) for p in paths}
    return {"current": True, "status": "CURRENT", "data_source": "PCS_CANONICAL_DATA",
            "research_id": report.get("research_id"), "symbol": report["symbol"],
            "calculation_version": report["calculation_version"], "files": files,
            "reason_codes": ["REPORT_SCHEMA_VALIDATED", "CANONICAL_INPUTS_HASHED", "CURRENT_ARTIFACT"]}
