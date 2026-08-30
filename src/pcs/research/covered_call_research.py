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
from .covered_call import select_contract, sell_call_timing_signal, audit_contract_candidates


class ReplayQuoteProvider:
    """Exact-contract quote lookups backed only by preloaded memory."""
    def __init__(self, *, data_access: PCSDataAccess | None = None):
        self.data_access = data_access
        self._quotes = {}
        self._dates = {}
        self.canonical_option_reads = 0
        self.quarter_load_count = 0
        self.lifecycle_storage_reads = 0
        self.quote_cache_hits = 0
        self.quote_cache_misses = 0

    def preload_quarter(self, symbol: str, year: int, quarter: int,
                        start: Any = None, end: Any = None) -> None:
        if self.data_access is None:
            raise ValueError("REPLAY_QUOTE_PROVIDER_ACCESS_REQUIRED")
        period = pd.Period(f"{int(year)}Q{int(quarter)}", freq="Q")
        columns = ["symbol", "trade_date", "expiration_date", "strike", "call_put",
                   "bid", "ask", "delta", "bid_iv", "ask_iv", "open_interest", "volume"]
        window_start = max(period.start_time.date(), pd.Timestamp(start).date()) if start is not None else period.start_time.date()
        window_end = min(period.end_time.date(), pd.Timestamp(end).date()) if end is not None else period.end_time.date()
        if window_start > window_end:
            return
        frame = self.data_access.read_quotes_for_windows(
            symbol, [(window_start, window_end)], columns=columns)
        self.canonical_option_reads += 1
        self.quarter_load_count += 1
        self.preload_frame(symbol, frame)

    def preload_frame(self, symbol: str, frame: pd.DataFrame,
                      chain_dates: set[Any] | None = None,
                      max_dte: int | None = None) -> None:
        """Index an already loaded bounded quarter without storage access."""
        if frame.empty:
            return
        frame = frame.copy()
        if "iv" not in frame.columns:
            iv_cols = [x for x in ("bid_iv", "ask_iv") if x in frame.columns]
            frame["iv"] = frame[iv_cols].mean(axis=1) if iv_cols else None
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
        wanted = None if chain_dates is None else {pd.Timestamp(x).normalize() for x in chain_dates}
        for day, group in frame.groupby("trade_date", sort=False):
            day_ts = pd.Timestamp(day).normalize()
            if wanted is None or day_ts in wanted:
                if max_dte is not None and not group.empty:
                    exp = pd.to_datetime(group["expiration_date"]).dt.normalize()
                    group = group.loc[
                        exp.ge(day_ts) & exp.le(day_ts + pd.Timedelta(days=int(max_dte))) &
                        group["call_put"].astype(str).str.lower().isin(["c", "call"])
                    ]
                contracts = _contracts_from_frame(group, symbol)
            else:
                contracts = []
            day_key = str(pd.Timestamp(day).date())
            if contracts:
                self._dates.setdefault(day_key, []).extend(contracts)
            if wanted is None or day_ts not in wanted:
                for row in group.itertuples(index=False):
                    if str(getattr(row, "call_put", "c")).lower() not in {"c", "call"}:
                        continue
                    expiration = pd.Timestamp(row.expiration_date).date()
                    if max_dte is not None and not 0 <= (expiration - day_ts.date()).days <= int(max_dte):
                        continue
                    key = (str(symbol).upper(), day_key, str(pd.Timestamp(row.expiration_date).date()),
                           float(row.strike), "c")
                    self._quotes[key] = {"bid": getattr(row, "bid", None), "ask": getattr(row, "ask", None),
                                         "delta": getattr(row, "delta", None), "iv": getattr(row, "iv", None),
                                         "volume": getattr(row, "volume", None),
                                         "open_interest": getattr(row, "open_interest", None),
                                         "quote_fresh": True}
            else:
                for contract in contracts:
                    key = (contract.symbol, day_key, str(contract.expiration), float(contract.strike), "c")
                    self._quotes[key] = {"bid": contract.bid, "ask": contract.ask,
                                         "delta": contract.delta, "iv": contract.iv,
                                         "volume": contract.volume, "open_interest": contract.open_interest,
                                         "quote_fresh": contract.quote_fresh}

    def get_quote(self, symbol: str, trade_date: Any, expiration: Any,
                  strike: float, option_type: str = "c") -> dict[str, Any] | None:
        key = (str(symbol).upper(), str(pd.Timestamp(trade_date).date()),
               str(pd.Timestamp(expiration).date()), float(strike), str(option_type).lower())
        value = self._quotes.get(key)
        if value is None:
            self.quote_cache_misses += 1
            return None
        self.quote_cache_hits += 1
        return dict(value)

    def has_quote(self, symbol: str, trade_date: Any, expiration: Any,
                  strike: float, option_type: str = "c") -> bool:
        """Return exact-key presence without counting a sparse-day lookup."""
        key = (str(symbol).upper(), str(pd.Timestamp(trade_date).date()),
               str(pd.Timestamp(expiration).date()), float(strike), str(option_type).lower())
        return key in self._quotes

    def quotes_by_date(self) -> dict[str, list[CoveredCallContract]]:
        return {key: list(value) for key, value in self._dates.items()}

    def instrumentation(self) -> dict[str, int]:
        return {"canonical_option_reads": self.canonical_option_reads,
                "quarter_load_count": self.quarter_load_count,
                "lifecycle_storage_reads": self.lifecycle_storage_reads,
                "quote_cache_hits": self.quote_cache_hits,
                "quote_cache_misses": self.quote_cache_misses}


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
        lifecycle_audit = []
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
                if obs.get("chain"):
                    quotes.setdefault(day, []).extend(obs["chain"])
                if obs.get("bid") is None or obs.get("ask") is None:
                    continue
                quotes.setdefault(day, []).append(CoveredCallContract(
                    str(symbol).upper(), day, obs["expiration"], float(entry["strike"]),
                    float(obs["bid"]), float(obs["ask"]), dte=(date.fromisoformat(obs["expiration"][:10]) - date.fromisoformat(day)).days))
            engine = CoveredCallDailyEngine(str(symbol), profit_capture=profit_capture,
                                             minimum_holding_days=minimum_holding_days,
                                             remaining_dte_condition=remaining_dte_condition)
            replay = engine.run(daily, quotes_by_date=quotes)
            completed_for_entry = [e for e in replay["completed_episodes"] if e.opened_date == entry_day]
            audited_episode = next((e for e in replay["episodes"] if e.opened_date == entry_day), None)
            if completed_for_entry:
                terminal = next((a.get("action") for a in reversed(replay["actions"])
                                 if a.get("episode_id") == completed_for_entry[0].episode_id and
                                 a.get("action") in {"CLOSE", "EXPIRE_WORTHLESS", "ASSIGNED"}), "CLOSE")
                lifecycle_audit.append({"entry_date": entry_day, "expiration": entry["expiration"],
                    "final_expiration": completed_for_entry[0].contract.expiration,
                    "roll_count": completed_for_entry[0].roll_count,
                    "current_strike": completed_for_entry[0].contract.strike,
                    "final_bid": completed_for_entry[0].contract.bid,
                    "final_ask": completed_for_entry[0].contract.ask,
                    "cumulative_credits": completed_for_entry[0].cumulative_premium_received,
                    "cumulative_btc_cost": completed_for_entry[0].cumulative_buyback_cost,
                    "strike": float(entry["strike"]), "underlying_entry": item["stock_entry_price"],
                    "underlying_expiration": item["observations"][-1]["underlying_close"],
                    "entry_premium": float(entry["bid"]) * 100, "final_action": terminal,
                    "status": "COMPLETED"})
            elif replay["conflicts"]:
                lifecycle_audit.append({"entry_date": entry_day, "expiration": entry["expiration"],
                    "final_expiration": audited_episode.contract.expiration if audited_episode else entry["expiration"],
                    "roll_count": audited_episode.roll_count if audited_episode else 0,
                    "current_strike": audited_episode.contract.strike if audited_episode else entry["strike"],
                    "final_bid": audited_episode.contract.bid if audited_episode else None,
                    "final_ask": audited_episode.contract.ask if audited_episode else None,
                    "cumulative_credits": audited_episode.cumulative_premium_received if audited_episode else None,
                    "cumulative_btc_cost": audited_episode.cumulative_buyback_cost if audited_episode else None,
                    "strike": float(entry["strike"]), "underlying_entry": item["stock_entry_price"],
                    "underlying_expiration": item["observations"][-1]["underlying_close"],
                    "entry_premium": float(entry["bid"]) * 100, "final_action": "CONFLICT",
                    "status": "LIFECYCLE_ERROR"})
            else:
                rolled_open = bool(audited_episode and audited_episode.roll_count)
                lifecycle_audit.append({"entry_date": entry_day, "expiration": entry["expiration"],
                    "final_expiration": audited_episode.contract.expiration if audited_episode else entry["expiration"],
                    "roll_count": audited_episode.roll_count if audited_episode else 0,
                    "current_strike": audited_episode.contract.strike if audited_episode else entry["strike"],
                    "final_bid": audited_episode.contract.bid if audited_episode else None,
                    "final_ask": audited_episode.contract.ask if audited_episode else None,
                    "cumulative_credits": audited_episode.cumulative_premium_received if audited_episode else None,
                    "cumulative_btc_cost": audited_episode.cumulative_buyback_cost if audited_episode else None,
                    "strike": float(entry["strike"]), "underlying_entry": item["stock_entry_price"],
                    "underlying_expiration": item["observations"][-1]["underlying_close"],
                    "entry_premium": float(entry["bid"]) * 100,
                    "final_action": "ROLLED" if rolled_open else "OPEN",
                    "status": "OPEN_AT_REPLAY_END"})
            for episode in replay["completed_episodes"]:
                stock_pnl = ((float(episode.close_underlying_price) - episode.stock_entry_price) * episode.shares
                             if episode.close_underlying_price is not None else None)
                call_pnl = episode.realized_cashflow - (stock_pnl or 0.0)
                terminal_actions = [a for a in replay["actions"]
                                    if a.get("episode_id") == episode.episode_id and
                                    a.get("action") in {"CLOSE", "EXPIRE_WORTHLESS", "ASSIGNED"}]
                terminal_action = terminal_actions[-1]["action"] if terminal_actions else "BUY_TO_CLOSE"
                assigned = bool(getattr(episode, "assigned", False))
                settlement_spot = getattr(episode, "settlement_underlying_price", None)
                roll_close_cost = sum(float(x.get("buyback_cost", 0) or 0) for x in episode.roll_history)
                roll_open_credit = sum(float(x.get("new_call_proceeds", 0) or 0) for x in episode.roll_history)
                final_btc_cost = max(float(episode.cumulative_buyback_cost) - roll_close_cost, 0.0)
                entry_credit = float(episode.cumulative_premium_received) - roll_open_credit
                expiration_settlement = 0.0
                assignment_settlement = 0.0
                ledger_pnl = (entry_credit - final_btc_cost - roll_close_cost +
                              roll_open_credit - expiration_settlement - assignment_settlement)
                trades.append({"symbol": episode.symbol, "entry_date": episode.opened_date,
                               "exit_date": episode.close_date, "holding_days":
                               (date.fromisoformat(episode.close_date[:10]) - date.fromisoformat(episode.opened_date[:10])).days,
                               "roll_count": len(episode.roll_history), "roll_credits": episode.cumulative_roll_credits,
                               "combined_pnl": episode.final_pnl, "stock_pnl": stock_pnl,
                               "call_premium": episode.cumulative_premium_received,
                               "call_realized_pnl": call_pnl,
                               "initial_call_premium": episode.current_contract.bid * episode.shares if episode.roll_count == 0 else None,
                               "btc_cost": episode.cumulative_buyback_cost,
                               "normal_btc_cost": final_btc_cost,
                               "roll_close_cost": roll_close_cost,
                               "roll_open_credit": roll_open_credit,
                               "call_cashflow_ledger": {
                                   "entry_credit": entry_credit,
                                   "btc_debit": final_btc_cost,
                                   "roll_close_debit": roll_close_cost,
                                   "roll_open_credit": roll_open_credit,
                                   "expiration_settlement": expiration_settlement,
                                   "assignment_settlement": assignment_settlement,
                                   "realized_call_pnl": call_pnl,
                                   "formula_pnl": ledger_pnl,
                                   "reconciles": abs(ledger_pnl - call_pnl) <= 0.01},
                               "entry_credit": entry_credit,
                               "btc_debit": final_btc_cost,
                               "roll_close_debit": roll_close_cost,
                               "roll_open_credit": roll_open_credit,
                               "expiration_settlement": expiration_settlement,
                               "assignment_settlement": assignment_settlement,
                               "realized_call_pnl": call_pnl,
                               "forced_btc_cost": final_btc_cost if episode.forced_btc else 0.0,
                               "forced_btc_loss": (final_btc_cost - float(episode.final_buyback_price or 0) * episode.shares)
                                                   if episode.forced_btc else 0.0,
                               "fees": sum(float(x.get("close_leg_fees", 0) or 0) + float(x.get("open_leg_fees", 0) or 0)
                                           for x in episode.roll_history),
                               "realized_option_pnl": call_pnl,
                               "buy_and_hold_pnl": ((float(episode.close_underlying_price) - episode.stock_entry_price) * episode.shares
                                                    if episode.close_underlying_price is not None else None),
                               "upside_sacrificed": max((float(settlement_spot) - float(episode.contract.strike)) * episode.shares, 0.0) if assigned and settlement_spot is not None else 0.0,
                               "exit_state": "FORCED_BTC_TO_PROTECT_SHARES" if episode.forced_btc else ("ASSIGNED" if assigned else ("EXPIRE_WORTHLESS" if terminal_action == "EXPIRE_WORTHLESS" else "BUY_TO_CLOSE"))})
            for conflict in replay["conflicts"]:
                episode = next(e for e in replay["episodes"] if e.episode_id == conflict["episode_id"])
                trades.append({"symbol": episode.symbol, "entry_date": episode.opened_date,
                               "exit_date": None, "roll_count": len(episode.roll_history),
                               "roll_credits": episode.cumulative_roll_credits,
                               "exit_state": "HARD_CONSTRAINT_CONFLICT", "status": "HARD_CONSTRAINT_CONFLICT"})
        return {"module": "pcs.research.covered_call_research", "version": "2.0",
                "symbol": str(symbol).upper(), "status": "COMPLETED" if trades else "NO_COMPLETED_TRADES",
                "data_source": "PCS_CANONICAL_DATA", "unified_lifecycle": True,
                "trades": trades, "lifecycle_audit": lifecycle_audit, "metrics": aggregate_metrics(trades),
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


def run_covered_call_portfolio(symbols: Iterable[str], *, start_date: Any = None,
                               end_date: Any = None,
                               entries_by_symbol: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
                               shares_by_symbol: Mapping[str, int] | None = None,
                               data_access: PCSDataAccess | None = None) -> dict[str, Any]:
    """Run the shared aggregation boundary independently for each ticker.

    This API never invents entries or falls back to non-canonical data.  When
    entries are supplied they must already be PIT-safe canonical selections;
    absent entries produce an explicit profile/preflight result.
    """
    from .covered_call_profiles import resolve_covered_call_profile
    access = data_access or PCSDataAccess.canonical()
    reports = {}
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        shares = int((shares_by_symbol or {}).get(symbol, 100))
        capacity = shares // 100
        if capacity < 1:
            reports[symbol] = {"symbol": symbol, "status": "WAIT", "profile_status": "NOT_APPLICABLE",
                               "capacity": capacity, "shares": shares, "metrics": {}, "trades": [],
                               "reason_codes": ["WAIT_NO_COVERED_CAPACITY"]}
            continue
        profile = resolve_covered_call_profile(symbol)
        try:
            from pcs.data.covered_call_readiness import resolve_ticker_data_readiness
            readiness = resolve_ticker_data_readiness(symbol, access=access)
            preflight = readiness.to_dict()
            if readiness.covered_call_ready and start_date is not None and end_date is not None:
                daily = access.read_prices(symbol, start_date, end_date)
                preflight["daily_rows"] = len(daily)
        except Exception as exc:
            preflight = {"status": "DATA_BLOCKED", "reason_codes": ["CANONICAL_READINESS_FAILED", str(exc)]}
        if profile.status.value != "VALIDATED":
            reports[symbol] = {"symbol": symbol, "status": "PROFILE_BLOCKED", "profile_status": profile.status.value,
                               "capacity": capacity, "shares": shares, "preflight": preflight, "metrics": {}, "trades": [],
                               "reason_codes": list(profile.reason_codes)}
            continue
        entries = list((entries_by_symbol or {}).get(symbol, ()))
        reports[symbol] = run_covered_call_research(symbol, trades=entries,
                                                    as_of=end_date or date.today()) | {"preflight": preflight,
                                                    "profile_status": profile.status.value,
                                                    "capacity": capacity, "shares": shares}
    return {"module": "pcs.research.covered_call_portfolio", "version": "1.0",
            "status": "COMPLETED", "data_source": "PCS_CANONICAL_DATA",
            "symbols": list(reports), "reports": reports, "final_oos_read": False,
            "reason_codes": ["TICKER_ISOLATED", "CANONICAL_DATA_ONLY", "NO_AUTOMATIC_PROMOTION"]}


def reconcile_option_only_ledger(trades: Iterable[Mapping[str, Any]], *, tolerance: float = .01) -> dict[str, Any]:
    """Reconcile option-only cashflows without including stock appreciation."""
    rows = []
    for trade in trades:
        premium = float(trade.get("initial_call_premium", trade.get("call_premium", 0)) or 0)
        roll_new = float(trade.get("roll_new_premium", 0) or 0)
        btc = float(trade.get("btc_cost", trade.get("buyback_cost", 0)) or 0)
        roll_buyback = float(trade.get("roll_buyback_cost", 0) or 0)
        fees = float(trade.get("fees", 0) or 0)
        settlement = float(trade.get("option_settlement", 0) or 0)
        expected = premium + roll_new - btc - roll_buyback - fees + settlement
        reported = trade.get("realized_option_pnl", trade.get("option_pnl"))
        error = None if reported is None else expected - float(reported)
        row = {"symbol": trade.get("symbol"), "episode_id": trade.get("episode_id"),
               "initial_call_premium": premium, "roll_new_premium": roll_new,
               "btc_cost": btc, "roll_buyback_cost": roll_buyback, "fees": fees,
               "option_settlement": settlement, "option_only_pnl": expected,
               "reported_option_pnl": reported, "reconciliation_error": error,
               "status": "PASS" if error is None or abs(error) <= tolerance else "ACCOUNTING_RECONCILIATION_FAIL"}
        rows.append(row)
    errors = [r for r in rows if r["status"] != "PASS"]
    return {"module": "pcs.research.covered_call_option_reconciliation", "version": "1.0",
            "status": "ACCOUNTING_RECONCILIATION_FAIL" if errors else "PASS",
            "tolerance": tolerance, "trades": rows,
            "option_only_pnl": sum(r["option_only_pnl"] for r in rows),
            "stock_pnl_excluded": True, "error_count": len(errors),
            "reason_codes": ["OPTION_ONLY_SEPARATED", "STOCK_PNL_EXCLUDED",
                             "ROLL_FEES_INCLUDED", "TOLERANCE_ENFORCED"]}


def summarize_option_only_by_year(trades: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return yearly and full-period option-only accounting summaries."""
    from collections import defaultdict
    groups = defaultdict(list)
    for trade in trades:
        year = str(trade.get("year") or str(trade.get("entry_date", "UNKNOWN"))[:4])
        groups[year].append(trade)
    def summary(rows):
        recon = reconcile_option_only_ledger(rows)
        return {"initial_call_premium": sum(float(x.get("initial_call_premium", x.get("call_premium", 0)) or 0) for x in rows),
                "roll_new_premium": sum(float(x.get("roll_new_premium", 0) or 0) for x in rows),
                "btc_cost": sum(float(x.get("btc_cost", x.get("buyback_cost", 0)) or 0) for x in rows),
                "roll_buyback_cost": sum(float(x.get("roll_buyback_cost", 0) or 0) for x in rows),
                "fees": sum(float(x.get("fees", 0) or 0) for x in rows),
                "option_only_pnl": recon["option_only_pnl"], "contracts_opened": len(rows),
                "roll_count": sum(int(x.get("roll_count", 0) or 0) for x in rows),
                "assignment_count": sum(str(x.get("exit_state", "")).upper() == "ASSIGNED" for x in rows),
                "expiry_count": sum(str(x.get("exit_state", "")).upper() in {"EXPIRE", "EXPIRE_WORTHLESS"} for x in rows),
                "reconciliation_status": recon["status"]}
    yearly = {year: summary(rows) for year, rows in sorted(groups.items()) if year != "UNKN"}
    full = summary([row for rows in groups.values() for row in rows])
    return {"module": "pcs.research.covered_call_yearly_option_summary", "version": "1.0",
            "status": "PASS" if full["reconciliation_status"] == "PASS" else "ACCOUNTING_RECONCILIATION_FAIL",
            "yearly": yearly, "full_period": full, "stock_pnl_excluded": True,
            "reason_codes": ["YEARLY_OPTION_ONLY", "FULL_PERIOD_RECONCILIATION", "NO_STOCK_PNL_MIX"]}


def persist_covered_call_artifacts(*, output_dir: str | Path,
                                   timing_report: Mapping[str, Any] | None = None,
                                   contract_report: Mapping[str, Any] | None = None,
                                   trades: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Persist isolated canonical research artifacts transactionally."""
    import tempfile
    import shutil
    root = Path(output_dir); root.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=root.name + ".tmp-", dir=str(root.parent)))
    try:
        if timing_report is not None:
            (temp / "sell_timing_summary.json").write_text(json.dumps(timing_report, default=str, indent=2), encoding="utf-8")
            pd.DataFrame(timing_report.get("rows", [])).to_csv(temp / "sell_timing_candidates.csv", index=False)
        if contract_report is not None:
            (temp / "contract_selection_summary.json").write_text(json.dumps(contract_report, default=str, indent=2), encoding="utf-8")
            pd.DataFrame(contract_report.get("candidate_audit", [])).to_parquet(temp / "contract_candidates.parquet", index=False)
        trade_rows = list(trades)
        if trade_rows:
            pd.DataFrame(trade_rows).to_parquet(temp / "trade_ledger.parquet", index=False)
            (temp / "yearly_option_pnl.json").write_text(json.dumps(summarize_option_only_by_year(trade_rows), default=str, indent=2), encoding="utf-8")
        files = sorted(p.name for p in temp.iterdir())
        manifest = {"current": True, "data_source": "PCS_CANONICAL_DATA",
                    "files": files + ["artifact_manifest.json"],
                    "file_hashes": {name: hashlib.sha256((temp / name).read_bytes()).hexdigest() for name in files},
                    "reason_codes": ["ISOLATED_RESEARCH_OUTPUT", "ATOMIC_REPLACEMENT", "CANONICAL_DATA_ONLY"]}
        (temp / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if root.exists(): shutil.rmtree(root)
        temp.replace(root)
        return {"status": "CURRENT", "output_dir": str(root), "manifest": manifest}
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def validate_covered_call_artifacts(output_dir: str | Path) -> dict[str, Any]:
    """Validate a CURRENT artifact set before any research consumer reads it."""
    root = Path(output_dir); manifest_path = root / "artifact_manifest.json"
    if not manifest_path.is_file():
        return {"status": "STALE_ARTIFACT", "reason_codes": ["MANIFEST_MISSING"]}
    try: manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception: return {"status": "STALE_ARTIFACT", "reason_codes": ["MANIFEST_INVALID"]}
    if manifest.get("current") is not True or manifest.get("data_source") != "PCS_CANONICAL_DATA":
        return {"status": "STALE_ARTIFACT", "reason_codes": ["MANIFEST_NOT_CURRENT_OR_NON_CANONICAL"]}
    hashes = manifest.get("file_hashes", {}); mismatches = []
    for name in manifest.get("files", []):
        if name == "artifact_manifest.json": continue
        path = root / name
        if not path.is_file() or hashes.get(name) != hashlib.sha256(path.read_bytes()).hexdigest(): mismatches.append(name)
    return {"status": "CURRENT" if not mismatches else "STALE_ARTIFACT",
            "mismatches": mismatches, "reason_codes": ["ARTIFACT_HASHES_VALID"] if not mismatches else ["ARTIFACT_HASH_MISMATCH"]}


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
        bid_iv = getattr(row, "bid_iv", None); ask_iv = getattr(row, "ask_iv", None)
        iv_values = [float(x) for x in (bid_iv, ask_iv) if x is not None and float(x) > 0]
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
            dte=dte, iv=(sum(iv_values) / len(iv_values) if iv_values else
                        (float(getattr(row, "iv")) if getattr(row, "iv", None) is not None else None))))
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
    last_nvdl_signal = None
    for row in joined.to_dict("records"):
        signal = sell_call_timing_signal(stock={**row, "symbol": symbol}, market=row, config=cfg)
        if str(symbol).upper() == "NVDL":
            # NVDL is intentionally state-aware and independent of the NVDA
            # profile.  The classifier is descriptive research logic only.
            from .covered_call_decision import classify_nvdl_state
            nvdl_state = classify_nvdl_state(row)
            signal["nvdl_state"] = nvdl_state["state"]
            if nvdl_state["state"] in {"RALLY_ACCELERATION", "PULLBACK", "UNKNOWN"}:
                signal["action"] = "WAIT"
                signal["reason_codes"] = list(signal.get("reason_codes", [])) + [
                    "NVDL_STATE_WAIT_FIRST", f"NVDL_{nvdl_state['state']}"]
            elif row.get("close") is not None and row.get("atr") is not None:
                # NVDL's state matrix is the research signal.  Do not let the
                # generic NVDA/QQQ timing gate erase valid NVDL states.
                signal["action"] = "OPEN"
                signal["status"] = "SIGNAL"
                signal["reason_codes"] = ["NVDL_STATE_SIGNAL", f"NVDL_{nvdl_state['state']}"]
            if signal["action"] == "OPEN":
                day = pd.Timestamp(row["date"])
                if last_nvdl_signal is not None and (day - last_nvdl_signal).days < 21:
                    signal["action"] = "WAIT"
                    signal["reason_codes"] = list(signal["reason_codes"]) + ["NVDL_ENTRY_COOLDOWN_21D"]
                else:
                    last_nvdl_signal = day
        if signal["action"] == "OPEN":
            signals.append({"date": row["date"], "symbol": symbol.upper(),
                            "close": row.get("close"), "atr": row.get("atr"), **signal})
    selected = []
    def choose_nvdl(chain, candidate):
        from .covered_call_decision import NVDLResearchState
        state = str(candidate.get("nvdl_state", "UNKNOWN"))
        bands = {
            NVDLResearchState.RALLY_IV.value: (.18, 2.5),
            NVDLResearchState.RESISTANCE_STALL.value: (.22, 2.0),
            NVDLResearchState.NORMAL_UPTREND.value: (.15, 3.0),
        }
        delta, atr_floor = bands.get(state, (.15, 3.0))
        options = []
        for dte_value in range(21, 46):
            chosen = select_contract(chain, config=cfg, dte=dte_value,
                                     target_delta=delta, max_delta=delta,
                                     min_strike=float(candidate["close"]) + atr_floor * float(candidate["atr"]),
                                     selection_method=selection_method,
                                     underlying_price=float(candidate["close"]),
                                     atr=float(candidate["atr"]), target_moneyness=target_moneyness,
                                     target_atr_distance=target_atr_distance)
            if chosen is not None:
                options.append(chosen)
        return max(options, key=lambda c: (c.bid, -c.spread_pct)) if options else None
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
                chosen = (choose_nvdl(chain, candidate) if str(symbol).upper() == "NVDL" else select_contract(chain, config=cfg, dte=dte, target_delta=target_delta,
                                         max_delta=(.22 if str(symbol).upper() == "NVDL" else None),
                                         min_strike=(float(candidate["close"]) + 2.0 * float(candidate["atr"]) if str(symbol).upper() == "NVDL" else None),
                                         selection_method=selection_method,
                                         underlying_price=float(candidate.get("close")) if candidate.get("close") is not None else None,
                                         atr=float(candidate.get("atr")) if candidate.get("atr") is not None else None,
                                         target_moneyness=target_moneyness,
                                         target_atr_distance=target_atr_distance))
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
            chosen = (choose_nvdl(chain, candidate) if str(symbol).upper() == "NVDL" else select_contract(chain, config=cfg, dte=dte, target_delta=target_delta,
                                     max_delta=(.22 if str(symbol).upper() == "NVDL" else None),
                                     min_strike=(float(candidate["close"]) + 2.0 * float(candidate["atr"]) if str(symbol).upper() == "NVDL" else None),
                                     selection_method=selection_method,
                                     underlying_price=float(candidate.get("close")) if candidate.get("close") is not None else None,
                                     atr=float(candidate.get("atr")) if candidate.get("atr") is not None else None,
                                     target_moneyness=target_moneyness,
                                     target_atr_distance=target_atr_distance))
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


def run_sell_timing_research(symbol: str, daily: Any, market: Any, *,
                             config: CoveredCallResearchConfig | None = None,
                             iv_by_date: Mapping[Any, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Build a ticker-isolated PIT sell-timing funnel with an always-sell control."""
    import pandas as pd
    ticker = str(symbol).strip().upper()
    cfg = config or CoveredCallResearchConfig()
    if "date" not in daily.columns or "date" not in market.columns:
        raise ValueError("PIT_FEATURE_DATE_COLUMN_REQUIRED")
    stock = daily.copy(); stock["date"] = pd.to_datetime(stock["date"]).dt.normalize()
    mkt = market.copy(); mkt["date"] = pd.to_datetime(mkt["date"]).dt.normalize()
    joined = stock.merge(mkt, on="date", how="left", suffixes=("", "_market")).sort_values("date")
    rows = []
    for record in joined.to_dict("records"):
        iv = (iv_by_date or {}).get(record["date"], (iv_by_date or {}).get(str(record["date"].date()), {}))
        if iv.get("iv_rank") is not None:
            record["iv_rank"] = iv["iv_rank"]
        signal = sell_call_timing_signal(stock={**record, "symbol": ticker}, market=record, config=cfg)
        rows.append({"date": str(record["date"].date()), "symbol": ticker,
                     "conditional_action": "SELL_ELIGIBLE" if signal["action"] == "OPEN" else "WAIT",
                     "always_sell_action": "SELL_ELIGIBLE",
                     "reason_codes": signal.get("reason_codes", []),
                     "feature_status": signal.get("status")})
    funnel = {"TRADING_DAY": len(rows),
              "FEATURE_READY": sum(x["feature_status"] != "DATA_INSUFFICIENT" for x in rows),
              "TIMING_ELIGIBLE": sum(x["conditional_action"] == "SELL_ELIGIBLE" for x in rows),
              "ALWAYS_SELL_BASELINE": len(rows)}
    return {"module": "pcs.research.covered_call_sell_timing", "version": "1.0",
            "symbol": ticker, "status": "DESCRIPTIVE_ONLY", "data_source": "PCS_CANONICAL_DATA",
            "rows": rows, "funnel": funnel, "candidate_days": len(rows),
            "actual_sell_days": funnel["TIMING_ELIGIBLE"],
            "sell_frequency": funnel["TIMING_ELIGIBLE"] / len(rows) if rows else None,
            "baseline": "ALWAYS_SELL_BASELINE", "final_oos_read": False,
            "production_changes_allowed": False,
            "reason_codes": ["PIT_SAFE_FEATURES", "TICKER_ISOLATED", "CONTROL_GROUP_INCLUDED",
                             "NO_FUTURE_PNL_SELECTION", "RESEARCH_ONLY"]}


def run_contract_selection_research(symbol: str, entry_dates: Iterable[Any], *,
                                    data_access: PCSDataAccess | None = None,
                                    dte_targets: Iterable[int] = (7, 10, 14, 21, 30, 35, 45, 60),
                                    delta_targets: Iterable[float] = (.05, .10, .15, .20, .25, .30),
                                    underlying_by_date: Mapping[Any, float] | None = None,
                                    atr_by_date: Mapping[Any, float] | None = None,
                                    config: CoveredCallResearchConfig | None = None) -> dict[str, Any]:
    """Compare PIT contract-selection dimensions on frozen entry dates."""
    access = data_access or PCSDataAccess.canonical()
    cfg = config or CoveredCallResearchConfig()
    ticker = str(symbol).strip().upper(); audits = []; selections = []
    frozen_entry_dates = list(entry_dates)
    for raw_day in frozen_entry_dates:
        day = str(pd.Timestamp(raw_day).date())
        chain = read_pit_call_chain(ticker, day, data_access=access)
        spot = float((underlying_by_date or {}).get(raw_day, (underlying_by_date or {}).get(day, 0)))
        atr = (atr_by_date or {}).get(raw_day, (atr_by_date or {}).get(day))
        for target_dte in dte_targets:
            for target_delta in delta_targets:
                rows = audit_contract_candidates(chain, config=cfg, as_of=day,
                                                 target_dte=int(target_dte), target_delta=float(target_delta),
                                                 underlying_price=spot, atr=atr)
                audits.extend([{**row, "target_dte": int(target_dte), "target_delta": float(target_delta)} for row in rows])
                eligible = [row for row in rows if row["eligible"]]
                if eligible:
                    selections.append({"date": day, "target_dte": int(target_dte),
                                       "target_delta": float(target_delta),
                                       "selected": min(eligible, key=lambda row: row["candidate_rank"])})
    return {"module": "pcs.research.covered_call_contract_selection", "version": "1.0",
            "symbol": ticker, "status": "DESCRIPTIVE_ONLY", "data_source": "PCS_CANONICAL_DATA",
            "entry_dates": [str(pd.Timestamp(x).date()) for x in frozen_entry_dates],
            "candidate_audit": audits, "selections": selections,
            "final_oos_read": False, "production_changes_allowed": False,
            "reason_codes": ["FROZEN_ENTRY_DATES", "PIT_CHAIN_ONLY", "ALL_CANDIDATES_RETAINED",
                             "NO_FUTURE_PNL_SELECTION", "RESEARCH_ONLY"]}


def replay_selected_entries(symbol: str, entries: Iterable[Mapping[str, Any]], *,
                            data_access: PCSDataAccess | None = None,
                            quote_provider: ReplayQuoteProvider | None = None,
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
        engine = CoveredCallDailyEngine(symbol, profit_capture=profit_capture,
                                        close_when_itm=str(symbol).upper() == "NVDL")
        daily_rows: dict[str, dict[str, Any]] = {}
        quotes_by_date: dict[str, list[CoveredCallContract]] = {}
        # The selected-entry observations contain only the current contract.
        # Fetch the bounded management window once so the selector can see
        # later expirations and strikes at roll dates.
        if prepared and quote_provider is not None:
            quotes_by_date = quote_provider.quotes_by_date()
        elif prepared and hasattr(access, "read_quotes_for_windows"):
            first_day = min(pd.Timestamp(x["entry"]["date"]).normalize() for x in prepared)
            last_day = max(pd.Timestamp(x["entry"]["expiration"]).normalize() for x in prepared)
            cache_key = (str(symbol).upper(), str(first_day.date()), str(last_day.date()))
            cached = globals().setdefault("_UNIFIED_QUOTE_CACHE", {}).get(cache_key)
            if cached is None:
                chain = _read_quotes_chunked(
                    access, symbol, [(first_day, last_day)],
                    ["symbol", "trade_date", "expiration_date", "strike", "call_put",
                     "bid", "ask", "delta", "open_interest", "volume"])
                cached = {}
                if not chain.empty:
                    if not pd.api.types.is_datetime64_any_dtype(chain["trade_date"]):
                        chain["trade_date"] = pd.to_datetime(
                            chain["trade_date"], format="ISO8601", errors="coerce")
                    else:
                        chain["trade_date"] = chain["trade_date"].dt.normalize()
                    for day, frame in chain.groupby(chain.trade_date.dt.normalize()):
                        cached[str(pd.Timestamp(day).date())] = _contracts_from_frame(frame, symbol)
                globals()["_UNIFIED_QUOTE_CACHE"][cache_key] = cached
            quotes_by_date = {day: list(items) for day, items in cached.items()}
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
            buy_hold_pnl = stock_pnl
            excess_return = (total_pnl - buy_hold_pnl) if total_pnl is not None and buy_hold_pnl is not None else None
            completed.append({"symbol": episode.symbol, "entry_date": episode.opened_date,
                              "exit_date": episode.close_date, "episode_id": episode.episode_id,
                              "holding_days": holding_days,
                              "roll_count": len(episode.roll_history),
                              "roll_credits": episode.cumulative_roll_credits,
                              "combined_pnl": total_pnl, "stock_pnl": stock_pnl,
                              "buy_and_hold_pnl": buy_hold_pnl,
                              "excess_return_vs_buy_and_hold": excess_return,
                              "upside_sacrificed": max(-excess_return, 0.0) if excess_return is not None else None,
                              "call_premium": episode.cumulative_premium_received,
                              "call_realized_pnl": episode.realized_cashflow - (stock_pnl or 0.0),
                              "exit_state": "BUY_TO_CLOSE"})
        conflicted_ids = {x["episode_id"] for x in replay.get("conflicts", [])}
        conflict_reasons = {x["episode_id"]: x.get("reason_codes", []) for x in replay.get("conflicts", [])}
        for episode in replay["episodes"]:
            if episode.episode_id in conflicted_ids:
                completed.append({"symbol": episode.symbol, "entry_date": episode.opened_date,
                                  "exit_date": None, "episode_id": episode.episode_id,
                                  "roll_count": len(episode.roll_history),
                                  "roll_credits": episode.cumulative_roll_credits,
                                  "exit_state": "HARD_CONSTRAINT_CONFLICT",
                                  "status": "HARD_CONSTRAINT_CONFLICT",
                                  "economic_status": "EXCLUDED_FROM_NORMAL_PNL",
                                  "reason_codes": conflict_reasons.get(episode.episode_id,
                                                                        ["H1_NO_ASSIGNMENT"])})
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
