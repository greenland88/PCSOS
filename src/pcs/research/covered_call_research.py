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
from .covered_call import CoveredCallContract, CoveredCallPosition, CoveredCallState
from .covered_call import select_contract, sell_call_timing_signal


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
                                target_delta: float = .30, dte: int = 43) -> dict[str, Any]:
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
        if signal["action"] == "OPEN": signals.append({"date": row["date"], "symbol": symbol.upper(), **signal})
    selected = []
    bulk = None
    if signals and data_access is not None and hasattr(data_access, "read_quotes_for_windows"):
        signal_dates = pd.to_datetime([x["date"] for x in signals]).normalize()
        chunks = []
        for period, group in pd.Series(signal_dates).groupby(signal_dates.to_period("Q")):
            chunks.append(data_access.read_quotes_for_windows(symbol,
                [(group.min(), group.max())],
                columns=["symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask", "delta", "open_interest", "volume"]))
        bulk = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
        if not bulk.empty: bulk["trade_date"] = pd.to_datetime(bulk.trade_date).dt.normalize()
    for candidate in signals:
        chain = (_contracts_from_frame(bulk[bulk.trade_date.eq(pd.Timestamp(candidate["date"]).normalize())], symbol)
                 if bulk is not None else read_pit_call_chain(symbol, candidate["date"], data_access=data_access))
        chosen = select_contract(chain, config=cfg, dte=dte, target_delta=target_delta)
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


def replay_selected_entries(symbol: str, entries: Iterable[Mapping[str, Any]], *,
                            data_access: PCSDataAccess | None = None,
                            profit_capture: float = .60) -> dict[str, Any]:
    """Replay selected entries with canonical quotes and return standard metrics."""
    access = data_access or PCSDataAccess.canonical()
    rows = []
    for entry in entries:
        start, end = entry["date"], entry["expiration"]
        prices = access.read_prices(symbol, start, end)
        price_by_date = {str(pd.Timestamp(r.date).date()): float(r.close) for r in prices.itertuples()}
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
            replay = replay_covered_call(position, observations, profit_capture=profit_capture)
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


def build_transfer_matrix(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = []
    for report in reports:
        metrics = report.get("metrics", {})
        rows.append({"symbol": str(report.get("symbol", "")).upper(),
                     "trades": int(metrics.get("trades", 0) or 0),
                     "combined_pnl": float(metrics.get("combined_pnl", 0) or 0),
                     "excess_return": float(metrics.get("excess_return", 0) or 0)})
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
