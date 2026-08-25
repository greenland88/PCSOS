"""Canonical Stage 4A replay orchestration.

The runner owns sequencing and output contracts only.  Entry decisions and
lifecycle outcomes are supplied by the existing deterministic engines.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
import json
import hashlib
import os
import uuid
import pandas as pd

from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2
from pcs.entry.support_contract import SupportState
from pcs.models.market import MarketState
from pcs.research.annualized_metrics import annualized_performance_metrics
from pcs.research.stage4a_replay import audit_inputs, to_trade_candidate


class LifecycleReplay(Protocol):
    def __call__(self, candidate: dict[str, Any]) -> dict[str, Any]: ...


class MarketStateFactory(Protocol):
    def __call__(self, candidate: dict[str, Any]) -> Any: ...


REPO_ROOT = Path(__file__).resolve().parents[3]


def _strict_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temp, index=False)
        if len(pd.read_parquet(temp)) != len(frame):
            raise RuntimeError("STAGE4A_OUTPUT_VALIDATION_FAILED")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_text(value: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(value, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def canonical_market_state_factory(path: str | Path = "data/derived/canonical_pit_market_states.parquet") -> MarketStateFactory:
    """Return a fail-closed date-keyed factory for canonical PIT states."""
    if str(path).replace("\\", "/") == "data/derived/canonical_pit_market_states.parquet":
        path = REPO_ROOT / path
    frame = pd.read_parquet(path)
    required = {"date", "market_state", "pit_asof", "pit_status"}
    if not required.issubset(frame.columns):
        raise ValueError("MARKET_STATE_ARTIFACT_SCHEMA_INVALID")
    states = {}
    for row in frame.to_dict("records"):
        day = pd.Timestamp(row["date"]).normalize()
        if row["pit_status"] != "PIT_SAFE" or pd.Timestamp(row["pit_asof"]).normalize() > day:
            continue
        payload = json.loads(row["market_state"]) if isinstance(row["market_state"], str) else row["market_state"]
        if set(MarketState.model_fields) - set(payload):
            raise ValueError("MARKET_STATE_ARTIFACT_FIELDS_INCOMPLETE")
        states[day] = MarketState.model_validate(payload)

    def factory(candidate: dict[str, Any]) -> MarketState:
        day = pd.Timestamp(candidate["date"]).normalize()
        if day not in states:
            raise ValueError(f"MARKET_STATE_PIT_UNAVAILABLE:{day.date()}")
        return states[day]
    return factory


@dataclass(frozen=True)
class ReplayConfig:
    output_dir: Path = REPO_ROOT / "research_outputs/stage4a_full_replay_20260820"
    event_unsupported_state: str = "FUTURE_EVENT_WINDOW_UNSUPPORTED"


def _identity(row: dict[str, Any]) -> tuple:
    return (str(row["ticker"]), str(pd.Timestamp(row["date"]).date()),
            str(pd.Timestamp(row["expiration"]).date()), float(row["short_strike"]),
            float(row["long_strike"]))


def _decision_record(row: dict[str, Any], *, status: str, reason: str,
                     decision: Any = None, historical_replay_eligible: bool = True) -> dict[str, Any]:
    out = {"ticker": row["ticker"], "candidate_id": row["candidate_id"],
           "decision_date": row["date"], "expiration": row["expiration"],
           "short_strike": row["short_strike"], "long_strike": row["long_strike"],
           "entry_contract_version": row.get("entry_contract_version"),
           "support_state": row.get("support_state"), "support_level": row.get("support_level"),
           "event_state": row.get("event_state"), "historical_replay_eligible": historical_replay_eligible,
           "status": status, "reason": reason}
    if decision is not None:
        payload = decision.model_dump(mode="json") if hasattr(decision, "model_dump") else dict(decision)
        out.update({"action": payload.get("action"), "accepted": payload.get("action") == "OPEN",
                    "rejection_reasons": payload.get("reason_codes", []),
                    "decision": payload})
    else:
        out.update({"action": "WAIT", "accepted": False, "rejection_reasons": [reason]})
    return out


def run_stage4a_full_replay(population: pd.DataFrame, *, decision_engine: Any,
                            lifecycle_replay: LifecycleReplay | None = None,
                            market_state_factory: MarketStateFactory | None = None,
                            portfolio: dict[str, Any] | None = None,
                            event_calendar: pd.DataFrame | None = None,
                            context_factory: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                            config: ReplayConfig = ReplayConfig()) -> dict[str, Any]:
    """Replay one already-frozen population and persist the canonical outputs."""
    required = {"candidate_id", "ticker", "date", "expiration", "short_strike", "long_strike", "entry_contract_version"}
    missing = sorted(required - set(population.columns))
    if missing:
        raise ValueError("CONTRACT_FAILURE: missing frozen columns: " + ", ".join(missing))
    if population.candidate_id.duplicated().any():
        raise ValueError("IDENTITY_FAILURE: duplicate candidate_id")
    if population.entry_contract_version.ne(ENTRY_CONTRACT_V2).any():
        raise ValueError("CONTRACT_FAILURE: non-v2 candidate row")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    # Do not load a lifecycle artifact before knowing that a candidate was
    # accepted.  Rejected/event-unsupported populations must remain auditable
    # even when no downstream lifecycle artifact exists.
    portfolio = dict(portfolio or {})
    portfolio.setdefault("planned_risk", portfolio.get("planned_loss", 0.0))
    portfolio.setdefault("planned_loss", portfolio.get("planned_risk", 0.0))
    portfolio.setdefault("bucket_risk", {})
    portfolio.setdefault("ticker_risk", {})
    market_state_factory = market_state_factory or canonical_market_state_factory()
    decisions, opened, results = [], [], []

    def record_failure(row: dict[str, Any], *, status: str, reason: str) -> None:
        """Replace the existing decision for a candidate, never append a duplicate."""
        failure = _decision_record(row, status=status, reason=reason)
        candidate_id = row.get("candidate_id")
        for index in range(len(decisions) - 1, -1, -1):
            if decisions[index].get("candidate_id") == candidate_id:
                decisions[index] = failure
                return
        decisions.append(failure)

    ordered = population.sort_values(
        [c for c in ("date", "ticker", "expiration", "short_strike", "long_strike", "candidate_id") if c in population.columns],
        kind="mergesort",
    )
    for row in ordered.to_dict("records"):
        event_state = str(row.get("event_state", ""))
        if event_state == config.event_unsupported_state or not _strict_bool(row.get("historical_replay_eligible", True), default=True):
            decisions.append(_decision_record(row, status="EVENT_WINDOW_UNSUPPORTED", reason=config.event_unsupported_state, historical_replay_eligible=False)); continue
        support = str(row.get("support_state", ""))
        if support == SupportState.NO_SUPPORT:
            decisions.append(_decision_record(row, status="VALID_BUT_ENTRY_INELIGIBLE", reason="SUPPORT_GATE_FAIL")); continue
        if support == SupportState.SUPPORT_DATA_MISSING:
            decisions.append(_decision_record(row, status="DATA_FAILURE", reason="SUPPORT_DATA_MISSING")); continue
        try:
            candidate = to_trade_candidate(row)
            updates = {"entry_date": str(pd.Timestamp(row["date"]).date())}
            for name in ("bid", "ask", "long_bid", "long_ask", "long_option_volume", "long_open_interest"):
                if name in row:
                    updates[name] = row[name]
            if hasattr(candidate, "model_copy"):
                if context_factory is not None:
                    ctx = context_factory(row)
                    updates.update({"trend_snapshot": ctx.get("snapshot"), "trend_interpretation": ctx.get("interpretation"), "trend_score_result": ctx.get("trend_score")})
                candidate = candidate.model_copy(update=updates)
            decision = decision_engine.evaluate_candidate(candidate, market_state_factory(row), portfolio, event_calendar=event_calendar)
            accepted = getattr(decision, "action", None) == "OPEN"
            rec = _decision_record(row, status="REPLAYED", reason=getattr(decision, "reason", ""), decision=decision)
            decisions.append(rec)
            if not accepted:
                continue
            if lifecycle_replay is None:
                try:
                    from pcs.research.stage4a_lifecycle import Stage4ALifecycleReplayAdapter
                    lifecycle_replay = Stage4ALifecycleReplayAdapter.from_phase0()
                except Exception:
                    record_failure(row, status="DATA_FAILURE", reason="LIFECYCLE_REPLAYER_UNAVAILABLE")
                    continue
            opened_id = hashlib.sha256("|".join(map(str, _identity(row))).encode()).hexdigest()[:24]
            trade = {**row, "opened_trade_id": opened_id}
            lifecycle = lifecycle_replay(trade)
            if lifecycle.get("identity") not in (None, _identity(row)):
                record_failure(row, status="IDENTITY_FAILURE", reason="STAGE4A_ACCEPTED_SPREAD_IDENTITY_MISMATCH"); continue
            opened.append(trade); results.append({**trade, **lifecycle})
            reserved = float(getattr(decision, "planned_loss", 0.0) or getattr(decision, "planned_risk", 0.0) or 0.0)
            portfolio["planned_loss"] += reserved
            portfolio["planned_risk"] = portfolio["planned_loss"]
            bucket = str(row.get("correlation_bucket", "UNKNOWN"))
            portfolio["bucket_risk"][bucket] = portfolio["bucket_risk"].get(bucket, 0.0) + reserved
            ticker = str(row.get("ticker", "UNKNOWN")).upper()
            portfolio["ticker_risk"][ticker] = portfolio["ticker_risk"].get(ticker, 0.0) + reserved
        except Exception as exc:
            from pcs.research.stage4a_lifecycle import LifecycleAdapterError
            status = "DATA_FAILURE" if isinstance(exc, LifecycleAdapterError) else "CONTRACT_FAILURE"
            record_failure(row, status=status, reason=str(exc))
    decisions_df, opened_df, results_df = map(pd.DataFrame, (decisions, opened, results))
    _atomic_parquet(decisions_df, config.output_dir / "stage4a_candidate_decisions.parquet")
    _atomic_parquet(opened_df, config.output_dir / "stage4a_opened_trades.parquet")
    _atomic_parquet(results_df, config.output_dir / "stage4a_trade_results.parquet")
    summary = {"rows": len(population), "decisions": len(decisions_df), "opened": len(opened_df), "results": len(results_df), "decision_engine_evaluated": int((decisions_df.status == "REPLAYED").sum()) if len(decisions_df) else 0, "annualized": annualized_performance_metrics(results_df)}
    _atomic_text(json.dumps(summary, indent=2, default=str), config.output_dir / "stage4a_entry_funnel.json")
    if len(decisions_df):
        ticker_summary = decisions_df.groupby("ticker", as_index=False).agg(
            frozen=("candidate_id", "size"), decision_engine_evaluated=("status", lambda s: int((s == "REPLAYED").sum())),
            accepted=("accepted", "sum"), rejected=("accepted", lambda s: int((~s.fillna(False)).sum())))
    else:
        ticker_summary = pd.DataFrame(columns=["ticker", "frozen", "decision_engine_evaluated", "accepted", "rejected"])
    csv_temp = config.output_dir / f".stage4a_ticker_summary.{uuid.uuid4().hex}.tmp"
    try:
        ticker_summary.to_csv(csv_temp, index=False)
        os.replace(csv_temp, config.output_dir / "stage4a_ticker_summary.csv")
    finally:
        csv_temp.unlink(missing_ok=True)
    _atomic_text(
        "# Stage 4A Full Replay\n\n" + json.dumps(summary, indent=2, default=str) +
        "\n\nThis report is produced by the canonical orchestration boundary.\n",
        config.output_dir / "stage4a_full_replay_report.md")
    _atomic_text(json.dumps({"candidate_ids_unique": bool(decisions_df.candidate_id.is_unique) if len(decisions_df) else True, "opened_trade_ids_unique": bool(opened_df.opened_trade_id.is_unique) if len(opened_df) else True, "entry_contract_version": ENTRY_CONTRACT_V2}, indent=2), config.output_dir / "stage4a_validation.json")
    return summary


__all__ = ["ReplayConfig", "run_stage4a_full_replay", "canonical_market_state_factory"]
