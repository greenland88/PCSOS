"""End-to-end orchestration for the existing Covered Call evaluators."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping
from uuid import uuid4

from pcs.data.access import PCSDataAccess
from pcs.data.control_plane import ensure_market_data
from pcs.market_context import build_market_context
from pcs.research.covered_call_decision import (
    evaluate_covered_call, evaluate_covered_call_research_only,
    build_pit_entry_features,
)
from pcs.research.covered_call_research import read_pit_call_chain
from pcs.research.covered_call_profiles import resolve_covered_call_profile
from pcs.data.covered_call_readiness import EVENT_RISK_SYMBOL
from pcs.data.live_market_state import require_live_market_state
import pandas as pd


@dataclass
class CoveredCallExecutionTrace:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def add(self, step: str, status: str, **details: Any) -> None:
        self.steps.append({"step": step, "status": status, **details})


def _blocked(symbol: str, mode: str, trace: CoveredCallExecutionTrace,
             reason: str, detail: Any = None) -> dict[str, Any]:
    return {"symbol": symbol, "mode": mode, "system_status": "BLOCKED",
            "strategy_status": "NOT_RUN", "strategy_evaluated": False,
            "contract_selection_evaluated": False, "reason_codes": [reason],
            "detail": detail, "execution_trace": trace.steps, "run_id": trace.run_id}


def _load_event_context(symbol: str, day: str) -> dict[str, Any]:
    path = "data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv"
    try:
        frame = pd.read_csv(path)
        target = EVENT_RISK_SYMBOL.get(symbol, symbol)
        rows = frame[frame.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(target)]
        dates = pd.to_datetime(rows.get("event_date"), errors="coerce").dropna()
        upcoming = dates[(dates >= pd.Timestamp(day)) & (dates <= pd.Timestamp(day) + pd.Timedelta(days=7))]
        return {"earnings_status": "KNOWN", "earnings_date": upcoming.min().date().isoformat()} if not upcoming.empty else {"earnings_status": "NO_EVENT"}
    except Exception as exc:
        return {"earnings_status": "UNAVAILABLE",
                "event_source_status": "EVENT_DATA_UNAVAILABLE",
                "event_source_error": f"{type(exc).__name__}: {exc}"}


def execute_covered_call_request(symbol: str, mode: str = "eod", *,
                                 as_of: str | None = None,
                                 research_only: bool = False,
                                 overrides: Mapping[str, Any] | None = None,
                                 adapters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Prepare all dependencies, then invoke the canonical CC evaluator."""
    s = str(symbol).strip().upper(); m = str(mode).lower(); o = dict(overrides or {})
    a = dict(adapters or {}); trace = CoveredCallExecutionTrace()
    if m not in {"eod", "live"}:
        return _blocked(s, m, trace, "UNSUPPORTED_EXECUTION_MODE")
    access = a.get("data_access") or PCSDataAccess(manifest_path=o.get("manifest_path", "data/manifests/storage_manifest.csv"), parquet_root=o.get("parquet_root", "data/parquet"))
    try:
        trace.add("REQUEST", "RESOLVED", symbol=s, mode=m, research_only=research_only)
        if not as_of:
            return _blocked(s, m, trace, "DECISION_AS_OF_REQUIRED")
        from pcs.data.strategy_readiness import StrategyDataRequirements, ensure_strategy_ready
        readiness_mode = "LIVE" if m == "live" else "HISTORICAL"
        readiness = ensure_strategy_ready(s, "COVERED_CALL", as_of, readiness_mode, StrategyDataRequirements(option_right="CALL", target_dte_min=7, target_dte_max=45), data_access=access)
        if readiness.data_status != "READY":
            trace.add("READINESS", "BLOCKED", data_reason=readiness.data_reason)
            return {"symbol": s, "mode": m, "system_status": "BLOCKED",
                    "action": "DATA_BLOCKED", "data_reason": readiness.data_reason,
                    "coverage": readiness.to_dict(), "strategy_status": "NOT_RUN",
                    "strategy_evaluated": False, "contract_selection_evaluated": False,
                    "execution_trace": trace.steps, "run_id": trace.run_id}
        handle = readiness.verified_data_handle
        if handle is None:
            return _blocked(s, m, trace, "VERIFIED_DATA_HANDLE_MISSING")
        readiness_generation_ids = {
            "underlying": handle.underlying_handle.generation_id,
            "options": handle.options_handle.generation_id,
        }
        try:
            daily_frames = [access.read_pinned_generation(handle.underlying_handle.dataset,
                                                           handle.underlying_handle.ticker,
                                                           p, handle.underlying_handle.generation_id)
                            for p in handle.underlying_handle.partitions]
            option_frames = [access.read_pinned_generation(handle.options_handle.dataset,
                                                            handle.options_handle.ticker,
                                                            p, handle.options_handle.generation_id)
                             for p in handle.options_handle.partitions]
            pinned_daily = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
            pinned_options = pd.concat(option_frames, ignore_index=True) if option_frames else pd.DataFrame()
        except Exception as exc:
            return _blocked(s, m, trace, "PINNED_READ_FAILED", str(exc))
        if pinned_daily.empty or pinned_options.empty:
            return _blocked(s, m, trace, "PINNED_DATA_EMPTY")
        class _PinnedAccess:
            def read_prices(self, symbol, start_date=None, end_date=None):
                if str(symbol).upper() == s:
                    return pinned_daily.copy()
                return access.read_prices(symbol, start_date=start_date, end_date=end_date)
            def read_option_chain(self, symbol, trade_date, expiration=None):
                if str(symbol).upper() == s:
                    frame = pinned_options.copy()
                    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
                    frame = frame[frame.trade_date.eq(pd.Timestamp(trade_date).normalize())]
                    if expiration is not None:
                        frame = frame[pd.to_datetime(frame.expiration_date, errors="coerce").dt.normalize().eq(pd.Timestamp(expiration).normalize())]
                    return frame
                return access.read_option_chain(symbol, trade_date, expiration)
        pinned_access = _PinnedAccess()
        # Readiness is the sole discovery/fetch/promotion boundary.  A second
        # ensure_market_data call here could promote a different snapshot
        # after the handle was verified and make the final decision
        # non-reproducible.
        cutoff = as_of
        trace.add("CANONICAL_DATA", "READY",
                  readiness_generation_ids={
                      "underlying": readiness.verified_data_handle.underlying_generation_id,
                      "options": readiness.verified_data_handle.options_generation_id,
                  })
        prices = pinned_daily.copy()
        if prices.empty:
            return _blocked(s, m, trace, "DAILY_DATA_UNAVAILABLE")
        effective = str(prices.date.max())[:10]
        position = a.get("position_loader", lambda symbol, day: None)(s, effective)
        if position is None and "shares_owned" in o:
            position = {"shares_owned": o.get("shares_owned"), "active_calls": o.get("active_calls", 0), "source": "override"}
        if position is None:
            trace.add("POSITION_CONTEXT", "NOT_PROVIDED", reason_code="EXECUTION_CAPACITY_UNKNOWN")
            position = {"shares_owned": None, "active_calls": 0, "source": "not_provided"}
        else:
            trace.add("POSITION_CONTEXT", "READY", source=position.get("source", "adapter"))
        market = a.get("market_builder", lambda symbol, day: build_market_context(symbol, day, data_access=pinned_access))(s, effective)
        trace.add("MARKET_CONTEXT", "READY", data_timestamp=getattr(market, "data_timestamp", effective))
        event = a.get("event_loader", _load_event_context)(s, effective)
        trace.add("EVENT_CONTEXT", "READY")
        if str(event.get("event_source_status", "")).upper() == "EVENT_DATA_UNAVAILABLE":
            trace.add("EVENT_CONTEXT", "BLOCKED", reason_code="EVENT_DATA_UNAVAILABLE")
            return _blocked(s, m, trace, "EVENT_DATA_UNAVAILABLE", event)
        quotes = a.get("chain_loader", lambda symbol, day: read_pit_call_chain(symbol, day, data_access=pinned_access))(s, effective)
        trace.add("OPTION_CHAIN", "READY", quote_count=len(quotes))
        shares = position.get("shares_owned")
        active = int(position.get("active_calls", 0) or 0)
        market_dict = market.model_dump(mode="json") if hasattr(market, "model_dump") else dict(market)
        market_dict.setdefault("market_state", "UNKNOWN")
        if research_only:
            result = evaluate_covered_call_research_only(s, effective, data_access=pinned_access,
                market=market_dict, event_context=event, shares_owned=shares, active_calls=active,
                quotes=quotes)
            result["execution_trace"] = trace.steps
            if shares is None:
                result.setdefault("reason_codes", []).append("EXECUTION_CAPACITY_UNKNOWN")
                result["capacity_note"] = "100 shares required per covered-call contract."
            result.update({"system_status": "READY", "strategy_status": "EXECUTED",
                           "strategy_evaluated": True,
                           "contract_selection_evaluated": result.get("candidate_status") in {"RESEARCH_ONLY_CANDIDATE", "NO_CANDIDATE"},
                           "run_id": trace.run_id, "position_status": "PROVIDED" if shares is not None else "NOT_PROVIDED"})
            result["readiness_underlying_generation_id"] = readiness_generation_ids["underlying"]
            result["readiness_options_generation_id"] = readiness_generation_ids["options"]
            result["runner_underlying_generation_id"] = readiness_generation_ids["underlying"]
            result["runner_options_generation_id"] = readiness_generation_ids["options"]
            trace.add("STRATEGY", "EXECUTED")
            trace.add("CONTRACT_SELECTION", "EXECUTED" if result["contract_selection_evaluated"] else "NOT_RUN")
            result["execution_trace"] = trace.steps
            return result
        profile = resolve_covered_call_profile(s)
        result = evaluate_covered_call(s, effective, shares_owned=shares, active_calls=active,
            event_context=event, market_context=market_dict, data_access=pinned_access, profile=profile,
            allow_missing_position=True)
        result.update({"system_status": "READY", "strategy_status": "EXECUTED",
                       "strategy_evaluated": True,
                       "contract_selection_evaluated": result.get("decision") in {"SELL", "NO_SELL"},
                       "execution_trace": trace.steps, "run_id": trace.run_id,
                       "position_status": "PROVIDED" if shares is not None else "NOT_PROVIDED"})
        result["readiness_underlying_generation_id"] = readiness_generation_ids["underlying"]
        result["readiness_options_generation_id"] = readiness_generation_ids["options"]
        result["runner_underlying_generation_id"] = readiness_generation_ids["underlying"]
        result["runner_options_generation_id"] = readiness_generation_ids["options"]
        if shares is None:
            result.setdefault("reason_codes", []).append("EXECUTION_CAPACITY_UNKNOWN")
            result["capacity_note"] = "100 shares required per covered-call contract."
        trace.add("STRATEGY", "EXECUTED")
        trace.add("CONTRACT_SELECTION", "EXECUTED" if result["contract_selection_evaluated"] else "NOT_RUN")
        result["execution_trace"] = trace.steps
        return result
    except Exception as exc:
        return _blocked(s, m, trace, "EXECUTION_DEPENDENCY_FAILED", f"{type(exc).__name__}: {exc}")


__all__ = ["CoveredCallExecutionTrace", "execute_covered_call_request"]
