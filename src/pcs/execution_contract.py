"""Shared end-to-end strategy execution contract.

The contract owns dependency recovery and keeps system state separate from a
strategy result. Strategy adapters are injected so existing engines remain the
canonical implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from uuid import uuid4


@dataclass
class ExecutionTrace:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def record(self, name: str, status: str, **details: Any) -> None:
        self.steps.append({"step": name, "status": status, **details})


def execute_strategy_request(*, strategy: str, symbol: str, as_of: str,
                             dependency_resolver: Callable[[ExecutionTrace], Mapping[str, Any]],
                             strategy_executor: Callable[[Mapping[str, Any], ExecutionTrace], Mapping[str, Any]],
                             research_only: bool = False) -> dict[str, Any]:
    """Resolve dependencies, recover what is safe, then execute one strategy.

    A resolver must raise ``ExecutionBlocked`` only for a genuine external
    blocker. Returned strategy envelopes are required to state that evaluation
    occurred; otherwise the contract emits BLOCKED/NOT_RUN.
    """
    trace = ExecutionTrace()
    try:
        trace.record("DEPENDENCY_RESOLUTION", "START")
        deps = dict(dependency_resolver(trace))
        trace.record("DEPENDENCY_RESOLUTION", "READY")
        # A dependency resolver is the single readiness boundary.  Never let
        # an adapter continue into strategy code when it returned a non-ready
        # data state; otherwise stale/unavailable data is easily mislabeled as
        # a normal WAIT by downstream strategy envelopes.
        # Readiness is an explicit admission contract.  A resolver that omits
        # the status is incomplete and must not silently enter strategy code.
        data_status = str(deps.get("data_status", deps.get("status", "NOT_READY"))).upper()
        if data_status not in {"READY", "DATA_READY"}:
            reason = deps.get("data_reason") or deps.get("reason_code") or "DATA_BLOCKED"
            trace.record("STRATEGY", "NOT_RUN", data_status=data_status, reason_code=reason)
            return {"system_status": "BLOCKED", "action": "DATA_BLOCKED",
                    "strategy_decision": "NOT_RUN", "strategy_evaluated": False,
                    "contract_selection_evaluated": False, "symbol": symbol,
                    "strategy": strategy, "as_of": as_of,
                    "reason_codes": [str(reason)], "data_reason": str(reason),
                    "execution_trace": trace.steps, "run_id": trace.run_id}
        result = dict(strategy_executor(deps, trace))
        # A strategy adapter must never be able to relabel a data failure as
        # a READY strategy result by omitting envelope fields.
        if str(result.get("action", "")).upper() == "DATA_BLOCKED":
            trace.record("STRATEGY", "NOT_RUN", reason_code=result.get("data_reason", "DATA_BLOCKED"))
            return {"system_status": "BLOCKED", "action": "DATA_BLOCKED",
                    "strategy_decision": "NOT_RUN", "strategy_evaluated": False,
                    "contract_selection_evaluated": False, "symbol": symbol,
                    "strategy": strategy, "as_of": as_of,
                    "reason_codes": result.get("reason_codes", [result.get("data_reason", "DATA_BLOCKED")]),
                    "data_reason": result.get("data_reason", "DATA_BLOCKED"),
                    "execution_trace": trace.steps, "run_id": trace.run_id}
        evaluated = bool(result.get("strategy_evaluated", result.get("strategy_decision") not in (None, "NOT_RUN")))
        if not evaluated:
            return {"system_status": "BLOCKED", "strategy_decision": "NOT_RUN",
                    "strategy_evaluated": False, "contract_selection_evaluated": False,
                    "symbol": symbol, "strategy": strategy, "as_of": as_of,
                    "reason_codes": result.get("reason_codes", ["STRATEGY_NOT_EXECUTED"]),
                    "execution_trace": trace.steps, "run_id": trace.run_id}
        result.setdefault("system_status", "READY")
        result.setdefault("strategy_evaluated", True)
        result.setdefault("contract_selection_evaluated", False)
        result.setdefault("execution_trace", trace.steps)
        result.setdefault("run_id", trace.run_id)
        result.setdefault("mode", "RESEARCH_ONLY" if research_only else "PRODUCTION")
        return result
    except ExecutionBlocked as exc:
        trace.record("EXECUTION", "BLOCKED", reason_code=exc.reason_code)
        return {"system_status": "BLOCKED", "strategy_decision": "NOT_RUN",
                "strategy_evaluated": False, "contract_selection_evaluated": False,
                "symbol": symbol, "strategy": strategy, "as_of": as_of,
                "reason_codes": [exc.reason_code], "detail": str(exc),
                "execution_trace": trace.steps, "run_id": trace.run_id}


class ExecutionBlocked(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


__all__ = ["ExecutionTrace", "ExecutionBlocked", "execute_strategy_request"]
