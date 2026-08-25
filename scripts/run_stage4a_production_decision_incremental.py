"""Run Stage 4A decisions with canonical PIT data and durable receipts.

Missing canonical historical market-state inputs are blocked explicitly; this
script never creates a default ``MarketState`` to make a historical row pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter
from pathlib import Path

import pandas as pd

# The repository supports both ``python -m scripts...`` and the documented
# direct-script invocation from its root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pcs.data.access import PCSDataAccess, DataAccessError, DataQualityError
from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.entry.contract_v2 import normalize_price_confirmation
from pcs.features.expected_move import calculate_expected_move
from pcs.models.market import MarketState
from pcs.models.trade import TradeCandidate
from pcs.research.entry_confirmation import analyze_entry_confirmation
from pcs.research.stage4a_context import HistoricalTrendContextProvider
from pcs.research.stage4a_production_evaluation import (
    DecisionRowStatus, canonical_breadth, completion_is_valid,
    evaluate_partition, write_completed_partition,
)

OUT = Path("research_outputs/stage4a_production_rebase_20260820")
PARTS = OUT / "production_universe_partitions"
DEC = OUT / "production_decision_partitions"
EVENT = Path("data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv")
DEFAULT_MARKET_STATES = Path("data/derived/canonical_pit_market_states.parquet")


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "ABSENT"


def _load_market_states(path: Path | None) -> dict[tuple[str, pd.Timestamp], MarketState]:
    """Load a persisted canonical market state; never synthesize one."""
    if path is None or not path.exists():
        return {}
    frame = pd.read_parquet(path)
    required = {"date", "market_state", "pit_asof", "producer_version"}
    if not required.issubset(frame.columns):
        raise ValueError("CANONICAL_MARKET_STATE_SCHEMA_INVALID")
    states: dict[tuple[str, pd.Timestamp], MarketState] = {}
    for row in frame.to_dict("records"):
        day = pd.Timestamp(row["date"]).normalize()
        if pd.Timestamp(row["pit_asof"]).normalize() > day:
            raise ValueError("CANONICAL_MARKET_STATE_PIT_FAILURE")
        payload = json.loads(row["market_state"]) if isinstance(row["market_state"], str) else row["market_state"]
        missing = set(MarketState.model_fields) - set(payload)
        if missing:
            raise ValueError(f"CANONICAL_MARKET_STATE_FIELDS_MISSING:{','.join(sorted(missing))}")
        states[(str(row.get("symbol", "MARKET")).upper(), day)] = MarketState.model_validate(payload)
    return states


def _blocked(row: dict, status: DecisionRowStatus, *codes: str) -> dict:
    return {**row, "status": status.value, "accepted": False,
            "reason_codes": list(codes), "primary_reason": codes[0] if codes else status.value}


def build_row_evaluator(*, access: PCSDataAccess, market_states: dict, event_calendar: pd.DataFrame):
    """Build a cached exact-input evaluator.  It owns no raw-file access."""
    contexts: dict[str, HistoricalTrendContextProvider] = {}
    daily: dict[str, pd.DataFrame] = {}
    confirmation: dict[tuple[str, pd.Timestamp], float] = {}
    breadth: dict[tuple[str, pd.Timestamp, pd.Timestamp, float], tuple[int, int, dict]] = {}
    engine = DecisionEngine(load_rules())
    portfolio = {"planned_risk": 0.0, "planned_loss": 0.0,
                 "bucket_risk": {}, "ticker_risk": {}}

    def evaluate(row: dict) -> dict:
        ticker = str(row["ticker"]).upper()
        day, expiry = pd.Timestamp(row["date"]).normalize(), pd.Timestamp(row["expiration"]).normalize()
        market = market_states.get((ticker, day)) or market_states.get(("MARKET", day))
        if market is None:
            return _blocked(row, DecisionRowStatus.BLOCKED_CONTEXT_UNAVAILABLE, "CANONICAL_MARKET_STATE_UNAVAILABLE")
        try:
            key = (ticker, day, expiry, float(row["short_strike"]))
            if key not in breadth:
                breadth[key] = canonical_breadth(access, ticker, day, expiry, float(row["short_strike"]))
            n, later, breadth_provenance = breadth[key]
        except (DataAccessError, DataQualityError, FileNotFoundError, ValueError) as exc:
            return _blocked(row, DecisionRowStatus.BLOCKED_SOURCE_UNAVAILABLE, "CANONICAL_PIT_CHAIN_UNAVAILABLE", type(exc).__name__)
        except Exception as exc:
            return _blocked(row, DecisionRowStatus.BLOCKED_PIT_FAILURE, "CANONICAL_PIT_CHAIN_INVALID", type(exc).__name__)
        try:
            if ticker not in contexts:
                contexts[ticker] = HistoricalTrendContextProvider(ticker)
            context = contexts[ticker]({"date": day, "candidate_id": row["opportunity_id"]})
            if not context.get("available") or context.get("snapshot") is None:
                return _blocked(row, DecisionRowStatus.BLOCKED_CONTEXT_UNAVAILABLE, "TREND_CONTEXT_UNAVAILABLE")
            snapshot = context["snapshot"]
            support = getattr(snapshot.support, "nearest_support", None)
            if support is None:
                return _blocked(row, DecisionRowStatus.BLOCKED_SUPPORT_UNAVAILABLE, "NO_SUPPORT")
            if ticker not in daily:
                # Load the canonical series once; slice it at each decision
                # date so later rows never reuse an early as-of snapshot.
                daily[ticker] = access.read_prices(ticker)
                daily[ticker].date = pd.to_datetime(daily[ticker].date).dt.normalize()
            day_rows = daily[ticker][daily[ticker].date.eq(day)]
            if len(day_rows) != 1:
                return _blocked(row, DecisionRowStatus.BLOCKED_SOURCE_UNAVAILABLE, "UNDERLYING_PRICE_UNAVAILABLE")
            close = float(day_rows.iloc[0].close)
            confirmation.setdefault((ticker, day), normalize_price_confirmation(analyze_entry_confirmation(daily[ticker], day).confirmation_score))
            atr, dte = float(getattr(snapshot.support, "current_atr")), int((expiry - day).days)
            credit = float(row["short_bid"]) - float(row["long_ask"])
            expected_move = float(calculate_expected_move(close, float(row["short_strike"]), atr, dte).expected_move_1d)
        except (FileNotFoundError, ValueError, IndexError, TypeError) as exc:
            return _blocked(row, DecisionRowStatus.BLOCKED_CONTEXT_UNAVAILABLE, "ENTRY_CONTEXT_UNAVAILABLE", type(exc).__name__)
        candidate = TradeCandidate(
            ticker=ticker, expiration=str(expiry.date()), short_strike=float(row["short_strike"]), long_strike=float(row["long_strike"]),
            underlying_price=close, credit=credit, dte=dte, short_delta=float(row["short_delta"]), expected_move=expected_move,
            expected_move_1d=expected_move, support_level=float(support), option_volume=int(row["short_volume"]),
            open_interest=int(row["short_oi"]), bid_ask_pct=(float(row["short_ask"])-float(row["short_bid"]))/max((float(row["short_ask"])+float(row["short_bid"]))/2, 1e-12),
            nearby_strikes=n, later_expirations=later, business_quality=0, trend_score=float(getattr(context["trend_score"], "score", 0)),
            support_score=0, sector_alignment=0, price_confirmation=confirmation[(ticker, day)], atr=atr,
            bid=float(row["short_bid"]), ask=float(row["short_ask"]), long_bid=float(row["long_bid"]), long_ask=float(row["long_ask"]),
            long_option_volume=int(row["long_volume"]), long_open_interest=int(row["long_oi"]), entry_date=str(day.date()),
            trend_snapshot=snapshot, trend_interpretation=context["interpretation"], trend_score_result=context["trend_score"],
        )
        decision = engine.evaluate_candidate(candidate, market, portfolio, event_calendar=event_calendar)
        accepted = decision.action.value == "OPEN"
        if accepted:
            reserved = float(decision.planned_loss or decision.planned_risk or 0.0)
            portfolio["planned_loss"] += reserved
            portfolio["planned_risk"] = portfolio["planned_loss"]
            bucket = str(row.get("correlation_bucket", "UNKNOWN"))
            portfolio["bucket_risk"][bucket] = portfolio["bucket_risk"].get(bucket, 0.0) + reserved
            portfolio["ticker_risk"][ticker] = portfolio["ticker_risk"].get(ticker, 0.0) + reserved
        return {**row, "status": (DecisionRowStatus.EVALUATED_ACCEPTED if accepted else DecisionRowStatus.EVALUATED_REJECTED).value,
                "accepted": accepted, "reason_codes": list(decision.reason_codes),
                "primary_reason": decision.reason_codes[0] if decision.reason_codes else decision.reason,
                "dte": dte, "atr": atr, "close": close, "credit": credit,
                "credit_width_ratio": credit / float(row["spread_width"]) if float(row["spread_width"]) else 0.0,
                "nearby_strikes": n, "later_expirations": later, "support_state": "SUPPORT_FOUND",
                "breadth_provenance": breadth_provenance}
    return evaluate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-state-artifact", type=Path, default=DEFAULT_MARKET_STATES)
    parser.add_argument("--limit-partitions", type=int)
    args = parser.parse_args()
    run_id = f"stage4a-production-{uuid.uuid4().hex}"
    access, calendar = PCSDataAccess(), pd.read_csv(EVENT)
    calendar.attrs["historical_pit_required"] = True
    if "event_date_known_at_entry" not in calendar.columns and "known_at_entry" not in calendar.columns:
        raise RuntimeError("EVENT_CALENDAR_PIT_METADATA_MISSING")
    calculation_version = "|".join(("stage4a-production-evaluation-v2", f"rules={_file_digest(Path('config/pcs_rules.yaml'))}",
                                    f"events={_file_digest(EVENT)}", f"market_states={_file_digest(args.market_state_artifact)}"))
    evaluator = build_row_evaluator(access=access, market_states=_load_market_states(args.market_state_artifact), event_calendar=calendar)
    DEC.mkdir(parents=True, exist_ok=True)
    parts = sorted(PARTS.glob("*.parquet"))
    if args.limit_partitions is not None:
        parts = parts[:args.limit_partitions]
    results, receipts = [], []
    for partition in parts:
        source, target = pd.read_parquet(partition), DEC / partition.name
        if completion_is_valid(source, target, calculation_version=calculation_version):
            results.append(pd.read_parquet(target)); receipts.append(json.loads(target.with_suffix(".receipt.json").read_text(encoding="utf-8"))); continue
        result = evaluate_partition(source, evaluator)
        receipt = write_completed_partition(source, result, target, source_partition=partition.name, run_id=run_id,
                                            request_id=uuid.uuid4().hex, data_timestamp=pd.Timestamp.now(tz="UTC").isoformat(),
                                            calculation_version=calculation_version)
        results.append(result); receipts.append(receipt.to_dict())
    all_results = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    if len(parts) == len(list(PARTS.glob("*.parquet"))):
        from pcs.research.stage4a_production_reporting import write_final_reports
        write_final_reports(all_results, OUT, receipts, run_id=run_id)
    print(json.dumps({"module": "stage4a_production_decision_incremental", "version": "v2", "run_id": run_id,
                      "partitions": len(parts), "rows": len(all_results),
                      "statuses": dict(Counter(all_results.status.astype(str))) if not all_results.empty else {}}, indent=2))


if __name__ == "__main__":
    main()
