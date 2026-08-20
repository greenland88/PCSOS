from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from pcs.engine.decision_engine import DecisionEngine
from pcs.models.decision import Action, Decision

SCHEMA_VERSION = "1.0"
CALCULATION_VERSION = "paper-trading-v1"
MODULE_NAME = "paper_trading_daily"


class PaperTradingStatus(StrEnum):
    READY = "READY"
    NO_DATA = "NO_DATA"


class PaperTradeSnapshot(BaseModel):
    symbol: str
    source: str
    action: Action
    reason_codes: list[str] = Field(default_factory=list)
    reason: str
    market_regime: str
    total_score: float
    classification: str
    expiration: str
    short_strike: float
    long_strike: float
    underlying_price: float
    recommended_contracts: int
    estimated_credit: float
    planned_risk: float
    theoretical_max_loss: float
    flags: list[str] = Field(default_factory=list)
    roll_candidate: dict | None = None


class PaperTradingResult(BaseModel):
    module: str = MODULE_NAME
    version: str = SCHEMA_VERSION
    symbol: str = "PORTFOLIO"
    as_of: str
    status: PaperTradingStatus
    data_timestamp: str
    calculation_version: str = CALCULATION_VERSION
    run_id: str
    request_id: str
    reason_codes: list[str] = Field(default_factory=list)
    candidate_count: int
    position_count: int
    action_counts: dict[str, int]
    planned_risk_open: float
    theoretical_max_loss_open: float
    planned_risk_positions: float
    theoretical_max_loss_positions: float
    snapshots: list[PaperTradeSnapshot]
    explanation: str = ""


def run_daily_paper_trading(
    provider,
    rules: dict,
    *,
    as_of: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    output_dir: str | Path | None = None,
    sqlite_path: str | Path | None = None,
) -> PaperTradingResult:
    """Run a deterministic daily paper-trading snapshot from provider data."""

    observed_at = datetime.now(UTC).isoformat()
    effective_date = as_of or observed_at[:10]
    resolved_run_id = run_id or f"run_{uuid4().hex}"
    resolved_request_id = request_id or f"req_{uuid4().hex}"

    market = provider.get_market_state()
    portfolio = provider.get_portfolio()
    if "bucket_risk" not in portfolio:
        portfolio = portfolio | {"bucket_risk": {}}

    engine = DecisionEngine(rules)
    event_calendar = provider.get_event_calendar() if hasattr(provider, "get_event_calendar") else None
    snapshots: list[PaperTradeSnapshot] = []

    for candidate in provider.get_candidates():
        decision = engine.evaluate_candidate(candidate, market, portfolio, event_calendar=event_calendar)
        snapshots.append(_snapshot_from_decision(decision, source="candidate"))

    for position in provider.get_positions():
        decision = engine.evaluate_position(position, market)
        snapshots.append(_snapshot_from_decision(decision, source="position"))

    action_counts = {action.value: 0 for action in Action}
    for snapshot in snapshots:
        action_counts[snapshot.action.value] += 1

    status = PaperTradingStatus.READY if snapshots else PaperTradingStatus.NO_DATA
    reason_codes = [] if snapshots else ["NO_PROVIDER_RECORDS"]
    result = PaperTradingResult(
        as_of=effective_date,
        status=status,
        data_timestamp=observed_at,
        run_id=resolved_run_id,
        request_id=resolved_request_id,
        reason_codes=reason_codes,
        candidate_count=sum(1 for item in snapshots if item.source == "candidate"),
        position_count=sum(1 for item in snapshots if item.source == "position"),
        action_counts=action_counts,
        planned_risk_open=sum(item.planned_risk for item in snapshots if item.source == "candidate" and item.action == Action.OPEN),
        theoretical_max_loss_open=sum(item.theoretical_max_loss for item in snapshots if item.source == "candidate" and item.action == Action.OPEN),
        planned_risk_positions=sum(item.planned_risk for item in snapshots if item.source == "position"),
        theoretical_max_loss_positions=sum(item.theoretical_max_loss for item in snapshots if item.source == "position"),
        snapshots=snapshots,
        explanation="Daily deterministic PCS paper-trading snapshot.",
    )

    if output_dir is not None:
        write_paper_trading_outputs(result, output_dir)
    if sqlite_path is not None:
        record_paper_trading_result(result, sqlite_path)
    return result


def write_paper_trading_outputs(result: PaperTradingResult, output_dir: str | Path) -> dict[str, Path]:
    target = Path(output_dir) / result.as_of
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "paper_trading_snapshot.json"
    csv_path = target / "paper_trading_snapshots.csv"
    summary_path = target / "paper_trading_summary.csv"

    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    snapshot_rows = [
        {
            "as_of": result.as_of,
            "run_id": result.run_id,
            "source": item.source,
            "symbol": item.symbol,
            "action": item.action.value,
            "market_regime": item.market_regime,
            "total_score": item.total_score,
            "classification": item.classification,
            "recommended_contracts": item.recommended_contracts,
            "planned_risk": item.planned_risk,
            "theoretical_max_loss": item.theoretical_max_loss,
            "reason": item.reason,
            "reason_codes": "|".join(item.reason_codes),
            "flags": "|".join(item.flags),
        }
        for item in result.snapshots
    ]
    _write_csv(csv_path, snapshot_rows)
    _write_csv(
        summary_path,
        [
            {
                "as_of": result.as_of,
                "run_id": result.run_id,
                "status": result.status.value,
                "candidate_count": result.candidate_count,
                "position_count": result.position_count,
                "planned_risk_open": result.planned_risk_open,
                "theoretical_max_loss_open": result.theoretical_max_loss_open,
                "planned_risk_positions": result.planned_risk_positions,
                "theoretical_max_loss_positions": result.theoretical_max_loss_positions,
                **{f"{action.lower()}_count": count for action, count in result.action_counts.items()},
            }
        ],
    )
    return {"json": json_path, "snapshots_csv": csv_path, "summary_csv": summary_path}


def record_paper_trading_result(result: PaperTradingResult, sqlite_path: str | Path) -> None:
    path = Path(sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trading_runs (
              id INTEGER PRIMARY KEY,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              as_of TEXT NOT NULL,
              run_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              status TEXT NOT NULL,
              payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trading_runs(as_of, run_id, request_id, status, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (result.as_of, result.run_id, result.request_id, result.status.value, result.model_dump_json()),
        )
        conn.commit()


def _snapshot_from_decision(decision: Decision, *, source: str) -> PaperTradeSnapshot:
    return PaperTradeSnapshot(
        symbol=decision.ticker.upper(),
        source=source,
        action=decision.action,
        reason_codes=_reason_codes(decision),
        reason=decision.reason,
        market_regime=decision.market_regime,
        total_score=decision.total_score,
        classification=decision.classification.value,
        expiration=decision.expiration,
        short_strike=decision.short_strike,
        long_strike=decision.long_strike,
        underlying_price=decision.underlying_price,
        recommended_contracts=decision.recommended_contracts,
        estimated_credit=decision.estimated_credit,
        planned_risk=decision.planned_risk,
        theoretical_max_loss=decision.theoretical_max_loss,
        flags=decision.flags,
        roll_candidate=decision.roll_candidate,
    )


def _reason_codes(decision: Decision) -> list[str]:
    codes: list[str] = list(decision.reason_codes)
    reason = decision.reason.lower()
    if decision.market_regime == "RED":
        codes.append("MARKET_REGIME_BLOCKED")
    if decision.action == Action.WAIT and (
        "liquidity" in reason or any("volume" in flag or "bid/ask" in flag for flag in decision.flags)
    ):
        codes.append("LIQUIDITY_REJECTED")
    if decision.action == Action.WAIT and "buffer" in reason:
        codes.append("STRIKE_BUFFER_REJECTED")
    if decision.action == Action.WAIT and ("capacity" in reason or "sizing" in reason):
        codes.append("PORTFOLIO_CAPACITY_REJECTED")
    if decision.action == Action.OPEN:
        codes.append("OPEN_RULES_PASSED")
    if decision.action == Action.NO_TRADE and not codes:
        codes.append("NO_TRADE")
    if decision.action == Action.HOLD:
        codes.append("POSITION_HELD")
    if decision.action == Action.CLOSE:
        codes.append("POSITION_CLOSE_REQUIRED")
    if decision.action == Action.ROLL:
        codes.append("POSITION_ROLL_CANDIDATE")
    if decision.action == Action.WAIT and not codes:
        codes.append("OPPORTUNITY_WAIT")
    return codes


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
