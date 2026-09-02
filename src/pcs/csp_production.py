"""Production live decision boundary for single-leg cash-secured puts."""
from __future__ import annotations
from typing import Any
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.live_market_state import require_live_market_state  # compatibility symbol; readiness owns the gate
from pcs.market_context import build_market_context
from pcs.strategies.cash_secured_put import ShortPutContract, ShortPutContractSelector


def execute_cash_secured_put_request(symbol: str, *, decision_as_of: str,
                                     available_cash: float,
                                     max_assignment_shares: int = 100,
                                     current_risk: float = 0.0,
                                     max_risk: float = float("inf"),
                                     open_positions: int = 0,
                                     max_positions: int = 1,
                                     min_dte: int = 7, max_dte: int = 45,
                                     data_access: PCSDataAccess | None = None,
                                     selector: ShortPutContractSelector | None = None,
                                     rules: dict | None = None,
                                     **compat: Any) -> dict[str, Any]:
    """Evaluate a current single-leg CSP; never places an order."""
    current_risk = float(compat.pop("current_soxl_risk", current_risk))
    max_risk = float(compat.pop("max_soxl_risk", max_risk))
    if compat:
        raise TypeError(f"UNSUPPORTED_CSP_ARGUMENTS:{sorted(compat)}")
    if not decision_as_of:
        return {"strategy_type": "CASH_SECURED_PUT", "structure": "SINGLE_LEG", "symbol": str(symbol).upper(),
                "action": "DATA_BLOCKED", "decision": "NOT_RUN",
                "strategy_evaluated": False, "contract_selection_evaluated": False,
                "data_reason": "DECISION_AS_OF_REQUIRED",
                "reason_codes": ["DECISION_AS_OF_REQUIRED"]}
    s = str(symbol).strip().upper(); access = data_access or PCSDataAccess.canonical()
    from pcs.data.strategy_readiness import StrategyDataRequirements, ensure_strategy_ready
    readiness = ensure_strategy_ready(s, "CASH_SECURED_PUT", decision_as_of, "LIVE", StrategyDataRequirements(target_dte_min=min_dte, target_dte_max=max_dte, option_right="PUT"), data_access=access)
    if readiness.data_status != "READY":
        return {"strategy_type":"CASH_SECURED_PUT","symbol":s,"action":"DATA_BLOCKED","data_reason":readiness.data_reason,"coverage":readiness.to_dict(),"reason_codes":[readiness.data_reason or "DATA_BLOCKED"]}
    # `require_live_market_state` is intentionally not called here: readiness
    # owns that gate, and the runner must consume its pinned datasets.  A
    # second ticker-based live read here could silently select a different
    # snapshot than the one that passed the target-window gate.
    # Read only the generation-pinned datasets admitted by readiness.  A
    # second ticker-based live read here could silently select a different
    # snapshot than the one that passed the target-window gate.
    handle = readiness.verified_data_handle
    if handle is None:
        return {"strategy_type": "CASH_SECURED_PUT", "structure": "SINGLE_LEG", "symbol": s,
                "action": "DATA_BLOCKED", "data_reason": "VERIFIED_DATA_HANDLE_MISSING",
                "strategy_evaluated": False, "reason_codes": ["VERIFIED_DATA_HANDLE_MISSING"]}
    try:
        daily_frames = [access.read_pinned_generation(handle.underlying_handle.dataset,
                                                       handle.underlying_handle.ticker,
                                                       partition, handle.underlying_handle.generation_id)
                        for partition in handle.underlying_handle.partitions]
        option_frames = [access.read_pinned_generation(handle.options_handle.dataset,
                                                        handle.options_handle.ticker,
                                                        partition, handle.options_handle.generation_id)
                         for partition in handle.options_handle.partitions]
        pinned_daily = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
        pinned_options = pd.concat(option_frames, ignore_index=True) if option_frames else pd.DataFrame()
    except Exception as exc:
        return {"strategy_type": "CASH_SECURED_PUT", "structure": "SINGLE_LEG", "symbol": s,
                "action": "DATA_BLOCKED", "data_reason": "PINNED_READ_FAILED",
                "detail": str(exc), "strategy_evaluated": False,
                "reason_codes": ["PINNED_READ_FAILED"]}
    if pinned_daily.empty or pinned_options.empty:
        return {"strategy_type": "CASH_SECURED_PUT", "structure": "SINGLE_LEG", "symbol": s,
                "action": "DATA_BLOCKED", "data_reason": "PINNED_DATA_EMPTY",
                "strategy_evaluated": False, "reason_codes": ["PINNED_DATA_EMPTY"]}
    session = pd.to_datetime(pinned_daily["date"], errors="coerce").dt.normalize().max()
    pinned_options["trade_date"] = pd.to_datetime(pinned_options["trade_date"], errors="coerce").dt.normalize()
    pinned_options = pinned_options[pinned_options.trade_date.eq(session)].copy()
    if pinned_options.empty:
        return {"strategy_type": "CASH_SECURED_PUT", "structure": "SINGLE_LEG", "symbol": s,
                "action": "DATA_BLOCKED", "data_reason": "QUOTE_SESSION_MISMATCH",
                "strategy_evaluated": False, "reason_codes": ["QUOTE_SESSION_MISMATCH"]}
    required = {"expiration_date", "strike", "bid", "ask", "delta", "open_interest"}
    missing = sorted(required - set(pinned_options.columns))
    if missing:
        return {"strategy_type": "CASH_SECURED_PUT", "structure": "SINGLE_LEG", "symbol": s,
                "action": "DATA_BLOCKED", "data_reason": "REQUIRED_OPTION_FIELDS_MISSING",
                "detail": missing, "strategy_evaluated": False,
                "reason_codes": ["REQUIRED_OPTION_FIELDS_MISSING"]}
    class _PinnedLive:
        status = "READY"
        reason_codes = ()
        recovery = None
        required_session = str(session.date())
        daily = pinned_daily
        options = pinned_options
    live = _PinnedLive()
    base = {"strategy_type": "CASH_SECURED_PUT", "structure": "SINGLE_LEG", "symbol": s,
            "production_profile": {"name": "CSP_PRODUCTION_DEFAULT", "source": "pcs.strategies.cash_secured_put.ShortPutContractSelector"},
            "decision_as_of": decision_as_of, "data_timestamp": live.required_session,
            "strategy_evaluated": False, "contract_selection_evaluated": False,
            "decision": "WAIT", "reason_codes": list(live.reason_codes),
            "readiness_underlying_generation_id": handle.underlying_handle.generation_id,
            "readiness_options_generation_id": handle.options_handle.generation_id,
            "runner_underlying_generation_id": handle.underlying_handle.generation_id,
            "runner_options_generation_id": handle.options_handle.generation_id}
    if live.status != "READY":
        base["risk_diagnostics"] = {"data_readiness": live.recovery}
        return base
    class _PinnedAccess:
        def read_prices(self, symbol, start_date=None, end_date=None):
            if str(symbol).upper() == s:
                return pinned_daily.copy()
            return access.read_prices(symbol, start_date=start_date, end_date=end_date)
        def read_quotes(self, symbol, start_date, end_date, expirations=None, strikes=None):
            if str(symbol).upper() == s:
                return pinned_options.copy()
            return access.read_quotes(symbol, start_date, end_date, expirations=expirations, strikes=strikes)
    context = build_market_context(s, live.required_session, data_access=_PinnedAccess(), rules=rules)
    candidates = []
    for row in live.options.itertuples(index=False):
        side = str(row.call_put).lower()
        if side not in {"p", "put"}: continue
        candidates.append(ShortPutContract(symbol=s, quote_date=str(pd.Timestamp(row.trade_date).date()),
            expiration=str(pd.Timestamp(row.expiration_date).date()), strike=float(row.strike), bid=float(row.bid), ask=float(row.ask),
            delta=float(row.delta) if pd.notna(row.delta) else None, iv=float(row.bid_iv) if hasattr(row, "bid_iv") and pd.notna(row.bid_iv) else None,
            open_interest=int(row.open_interest) if pd.notna(row.open_interest) else None, volume=int(row.volume) if hasattr(row, "volume") and pd.notna(row.volume) else None,
            underlying_price=float(context.underlying_price), atr=float(context.atr14 or 0), support=context.support, pit_status="PIT_SAFE"))
    base["strategy_evaluated"] = True
    selected = (selector or ShortPutContractSelector()).select(candidates, available_cash=available_cash,
        max_assignment_shares=max_assignment_shares, current_risk=current_risk,
        max_risk=max_risk, open_positions=open_positions, max_positions=max_positions,
        expected_symbol=s)
    base["contract_selection_evaluated"] = True
    base["liquidity_diagnostics"] = {"candidate_count": len(selected.candidates), "candidates": list(selected.candidates)}
    if selected.contract is None:
        base["reason_codes"] = list(selected.reason_codes)
        return base
    c = selected.contract; premium = c.credit; collateral = c.collateral_required
    base.update({"selected_expiration": c.expiration, "selected_strike": c.strike, "delta": c.delta,
                 "bid": c.bid, "ask": c.ask, "mark": (c.bid + c.ask) / 2, "DTE": c.dte,
                 "ATR_distance": c.atr_distance, "support_distance": (c.strike - float(c.support)) if c.support is not None else None,
                 "cash_secured": collateral, "premium": premium, "cash_yield": premium / collateral if collateral else 0.0,
                 "annualized_cash_yield": (premium / collateral) * 365 / c.dte if collateral and c.dte else 0.0,
                 "cash_secured_requirement": collateral <= available_cash,
                 "risk_diagnostics": {"planned_loss": collateral, "assignment_risk": c.strike * 100,
                                      "portfolio_risk_available": max_risk - current_risk},
                 "decision": "OPEN", "reason_codes": ["CSP_ENTRY_GATES_PASSED"]})
    return base


__all__ = ["execute_cash_secured_put_request"]
