"""Explicit, research-only Cash-Secured Put lifecycle adapter.

The runner consumes already selected PIT inputs.  It never discovers dates,
reads files, calls a broker, or falls back to the PCS replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
from typing import Any, Iterable, Mapping

import pandas as pd
from pcs.data.access import PCSDataAccess

from pcs.strategies.cash_secured_put import (
    CashSecuredPutPosition, PutLifecycleState, ShortPutContract,
)
from .research_framework import ResearchSpec, ResearchSpecError, ResearchStatus, spec_hash

REQUIRED_ARTIFACTS = (
    "readiness_report.json", "daily_decision_packets.parquet",
    "candidate_contracts.parquet", "selected_trades.parquet",
    "lifecycle_results.parquet", "assignment_ledger.parquet",
    "collateral_segments.parquet",
    "yearly_metrics.json", "strategy_comparison.json", "artifact_manifest.json",
)
CALCULATION_VERSION = "cash_secured_put_lifecycle_v1"


def read_csp_artifacts(path: str | Path, spec: ResearchSpec) -> dict[str, Any]:
    """Read only a complete, current, hash-valid CSP artifact set."""
    root = Path(path)
    manifest_path = root / "artifact_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("STALE_ARTIFACT")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("current") is not True or manifest.get("status") != "CURRENT"
            or manifest.get("strategy_type") != "CASH_SECURED_PUT"
            or manifest.get("data_source") != "PCS_CANONICAL_DATA"
            or manifest.get("symbol") != spec.ticker
            or manifest.get("spec_hash") != spec_hash(spec)
            or manifest.get("final_oos_read") is not False):
        raise RuntimeError("STALE_ARTIFACT")
    for record in manifest.get("files", ()):
        file_path = root / record["path"]
        if not file_path.is_file() or hashlib.sha256(file_path.read_bytes()).hexdigest() != record.get("sha256"):
            raise RuntimeError("STALE_ARTIFACT")
    if set(REQUIRED_ARTIFACTS) - {x["path"] for x in manifest.get("files", ())} - {"artifact_manifest.json"}:
        raise RuntimeError("STALE_ARTIFACT")
    return manifest


@dataclass(frozen=True)
class CashSecuredPutRunResult:
    status: str
    reason_codes: tuple[str, ...]
    lifecycle_results: tuple[dict[str, Any], ...] = ()
    assignment_ledger: tuple[dict[str, Any], ...] = ()
    collateral_segments: tuple[dict[str, Any], ...] = ()
    artifact_dir: str | None = None


def _json_default(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _contract(value: ShortPutContract | Mapping[str, Any]) -> ShortPutContract:
    return value if isinstance(value, ShortPutContract) else ShortPutContract(**dict(value))


class CashSecuredPutLifecycleRunner:
    """Run lifecycle over caller-supplied selected entry contracts."""

    def __init__(self, spec: ResearchSpec, *, data_access=None):
        if spec.strategy_type.value != "CASH_SECURED_PUT":
            raise ResearchSpecError("STRATEGY_RUNNER_MISMATCH:CASH_SECURED_PUT")
        self.spec = spec
        self.data_access = data_access  # dependency injection only; never read here

    def run(self, *, entries: Iterable[Mapping[str, Any]] | None = None,
            daily_observations: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
            exact_quotes: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
            candidates: Iterable[Mapping[str, Any]] | None = None,
            trading_sessions_by_episode: Mapping[str, Iterable[str]] | None = None,
            output_dir: str | Path | None = None) -> CashSecuredPutRunResult:
        entries = list(entries or [])
        if not entries:
            result = CashSecuredPutRunResult(
                ResearchStatus.NO_SIGNAL_DATES.value,
                ("CSP_SIGNAL_ENGINE_NOT_CONFIGURED",),
            )
            if output_dir is not None:
                result = self.persist_result(result, output_dir=output_dir, candidates=list(candidates or []))
            return result
        daily_observations = daily_observations or {}
        exact_quotes = exact_quotes or {}
        candidates = list(candidates or [])
        trading_sessions_by_episode = trading_sessions_by_episode or {}
        lifecycle: list[dict[str, Any]] = []
        assignments: list[dict[str, Any]] = []
        all_segments: list[dict[str, Any]] = []
        for index, entry in enumerate(entries):
            original = _contract(entry["contract"])
            original_credit = float(entry["entry_credit"])
            original_collateral = original.strike * 100 - original_credit * 100
            position = CashSecuredPutPosition(original, original_credit)
            identity = str(entry.get("episode_id", f"{self.spec.ticker}:{index}"))
            actions = [{"episode_id": identity, "symbol": self.spec.ticker, "action": "OPEN",
                        "state_before": None, "state_after": position.state.value,
                        "date": original.quote_date, "quote_date": original.quote_date,
                        "expiration": original.expiration, "strike": original.strike,
                        "bid": original.bid, "ask": original.ask, "pit_status": original.pit_status,
                        "underlying_mark": original.underlying_price, "collateral": original_collateral,
                        "cumulative_premium": original_credit * 100, "cumulative_buyback_cost": 0.0,
                        "realized_pnl": None, "reason_codes": ["OPEN_AT_BID"]}]
            observations = list(daily_observations.get(identity, entry.get("observations", ())))
            dates = []
            for obs in observations:
                if "date" in obs and "quote_date" in obs and str(obs["date"])[:10] != str(obs["quote_date"])[:10]:
                    raise ValueError("OBSERVATION_DATE_CONFLICT")
                raw_date = obs.get("date", obs.get("quote_date"))
                if not raw_date:
                    raise ValueError("OBSERVATION_DATE_MISSING")
                try:
                    parsed = datetime.fromisoformat(str(raw_date)[:10])
                except ValueError as exc:
                    raise ValueError("OBSERVATION_DATE_INVALID") from exc
                date = parsed.date().isoformat()
                if date < original.quote_date[:10]: raise ValueError("OBSERVATION_BEFORE_ENTRY")
                if dates and date <= dates[-1]: raise ValueError("DUPLICATE_DAILY_OBSERVATION" if date == dates[-1] else "OBSERVATIONS_NOT_MONOTONIC")
                dates.append(date)
            sessions = tuple(sorted(str(x)[:10] for x in trading_sessions_by_episode.get(identity, ())))
            if observations and not sessions:
                raise ValueError("SESSION_CALENDAR_UNAVAILABLE")
            if len(set(sessions)) != len(sessions):
                raise ValueError("SESSION_CALENDAR_INVALID")
            session_set = set(sessions)
            for obs, date in zip(observations, dates):
                if "is_trading_session" in obs and bool(obs["is_trading_session"]) != (date in session_set):
                    raise ValueError("SESSION_MARKER_CALENDAR_CONFLICT")
            processed_observations = []
            gross_received = original_credit * 100.0
            buyback_cost = 0.0
            terminal_date = original.quote_date[:10]
            for observation in observations:
                state_before = position.state.value
                action = self._manage(position, observation, exact_quotes.get(identity, {}))
                processed_observations.append(observation)
                terminal_date = str(observation.get("date", terminal_date))[:10]
                actions.append({"episode_id": identity, "symbol": self.spec.ticker,
                                "state_before": state_before,
                                "state_after": position.state.value,
                                "expiration": position.contract.expiration, "strike": position.contract.strike,
                                "quote_date": position.contract.quote_date,
                                "bid": position.contract.bid, "ask": position.contract.ask,
                                "pit_status": position.contract.pit_status,
                                "collateral": position.collateral(),
                                "cumulative_premium": gross_received + float(action.get("premium_received", 0.0)),
                                "single_buyback_cost": float(action.get("buyback_cost", 0.0)),
                                "cumulative_buyback_cost": buyback_cost + float(action.get("buyback_cost", 0.0)),
                                "realized_pnl": action.get("pnl"),
                                "reason_codes": [action.pop("reason_code")] if action.get("reason_code") else [],
                                **action, "episode_id": identity})
                gross_received += float(action.get("premium_received", 0.0))
                buyback_cost += float(action.get("buyback_cost", 0.0))
                if position.state in {PutLifecycleState.PROFIT_CLOSE, PutLifecycleState.RISK_CLOSE,
                                      PutLifecycleState.EXPIRE_WORTHLESS, PutLifecycleState.ASSIGNMENT}:
                    break
            terminal = terminal_date
            segments = []
            leg_start = original.quote_date[:10]
            leg = 1
            leg_contract = original
            for a in actions[1:]:
                if a.get("action") == "ROLL":
                    end = str(a["date"])[:10]
                    days = max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(leg_start)).days)
                    ts = sum(1 for s in sessions if leg_start <= s < end)
                    credit = (float(a.get("cumulative_premium", 0.0)) - float(a.get("premium_received", 0.0))
                              - (float(a.get("cumulative_buyback_cost", 0.0)) - float(a.get("single_buyback_cost", 0.0))))
                    segments.append({"episode_id": identity, "segment_id": f"{identity}:seg{leg}", "leg_number": leg,
                                     "start_date": leg_start, "end_date": end, "start_action": "OPEN", "end_action": "ROLL",
                                     "strike": leg_contract.strike, "expiration": leg_contract.expiration,
                                     "cumulative_net_credit_per_share": credit / 100, "collateral_required_dollars": leg_contract.strike * 100 - credit,
                                     "premium_received_dollars": credit + float(a.get("single_buyback_cost", 0.0)),
                                     "buyback_cost_dollars": float(a.get("cumulative_buyback_cost", 0.0)) - float(a.get("single_buyback_cost", 0.0)),
                                     "calendar_days": days, "trading_days": ts,
                                     "collateral_calendar_days": (leg_contract.strike * 100 - credit) * days,
                                     "collateral_trading_days": (leg_contract.strike * 100 - credit) * ts})
                    leg_start = end; leg += 1
                    leg_contract = _contract({"symbol": self.spec.ticker, "quote_date": a.get("new_quote_date", end), "expiration": a["new_expiration"],
                                              "strike": a["new_strike"], "bid": a["new_open_bid"], "ask": a["new_open_bid"], "delta": 0, "iv": 0,
                                              "open_interest": 0, "volume": 0, "underlying_price": 0, "atr": 1, "support": 999})
            days = max(0, (datetime.fromisoformat(terminal) - datetime.fromisoformat(leg_start)).days)
            ts = sum(1 for s in sessions if leg_start <= s <= terminal)
            cumulative_credit = gross_received - buyback_cost
            collateral = leg_contract.strike * 100 - cumulative_credit
            segments.append({"episode_id": identity, "segment_id": f"{identity}:seg{leg}", "leg_number": leg,
                             "start_date": leg_start, "end_date": terminal, "start_action": "ROLL" if leg > 1 else "OPEN",
                             "end_action": position.state.value, "strike": leg_contract.strike, "expiration": leg_contract.expiration,
                             "cumulative_net_credit_per_share": cumulative_credit / 100, "collateral_required_dollars": collateral,
                             "premium_received_dollars": gross_received, "buyback_cost_dollars": buyback_cost,
                             "calendar_days": days, "trading_days": ts, "collateral_calendar_days": collateral * days,
                             "collateral_trading_days": collateral * ts})
            lifecycle.append({"episode_id": identity, "state": position.state.value,
                              "roll_count": position.roll_count, "cumulative_credit": position.entry_credit,
                              "entry_date": original.quote_date, "original_entry_date": original.quote_date,
                              "original_strike": original.strike, "original_expiration": original.expiration,
                              "original_entry_credit": original_credit, "original_collateral": original_collateral,
                              "gross_premium_received": gross_received,
                              "cumulative_buyback_cost": buyback_cost,
                              "net_option_pnl": gross_received - buyback_cost,
                              "assignment_stock_component": ((float(position.assignment.stock_mark) - float(position.assignment.assignment_price)) * 100.0) if position.assignment else 0.0,
                              "total_economic_pnl": (gross_received - buyback_cost + (float(position.assignment.stock_mark) - float(position.assignment.assignment_price)) * 100.0) if position.assignment else gross_received - buyback_cost,
                              "realized": position.state.value in {"PROFIT_CLOSE", "RISK_CLOSE", "EXPIRE_WORTHLESS", "ASSIGNMENT"},
                              "collateral_required": original_collateral,
                              "terminal_date": terminal_date,
                              "holding_calendar_days": max(0, (datetime.fromisoformat(terminal_date) - datetime.fromisoformat(original.quote_date[:10])).days),
                              "holding_trading_days": sum(1 for x in sessions if original.quote_date[:10] <= x <= terminal_date),
                              "collateral_calendar_days": sum(x["collateral_calendar_days"] for x in segments),
                              "collateral_trading_days": sum(x["collateral_trading_days"] for x in segments),
                              "session_calendar_source": "EXPLICIT_INPUT",
                              "session_calendar_status": "VALIDATED",
                              "session_count_used": sum(1 for x in sessions if original.quote_date[:10] <= x <= terminal_date),
                              "actions": actions})
            all_segments.extend(segments)
            if position.assignment:
                assignments.append({"episode_id": identity, **position.assignment.__dict__})
        result = CashSecuredPutRunResult("COMPLETED", ("CSP_LIFECYCLE_COMPLETED",), tuple(lifecycle), tuple(assignments), tuple(all_segments))
        if output_dir is not None:
            result = self.persist_result(result, output_dir=output_dir, candidates=candidates)
        return result

    def _manage(self, position: CashSecuredPutPosition, observation: Mapping[str, Any], quotes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        date = str(observation.get("date", observation.get("quote_date", "")))
        if observation.get("expire"):
            if date[:10] < position.contract.expiration[:10]:
                position.hold()
                return {"action": "HOLD", "date": date, "reason_code": "PREMATURE_EXPIRY_REJECTED"}
            value = position.expire(float(observation["underlying_mark"]), int(observation.get("holding_days", 0)))
            numeric = float(value.total_economic_pnl if hasattr(value, "total_economic_pnl") else value)
            return {"action": position.state.value, "date": date, "pnl": numeric, "premium_received": 0.0, "buyback_cost": 0.0}
        if observation.get("roll"):
            old_contract = position.contract
            new_contract = quotes.get(str(observation["roll"]))
            if new_contract is None or observation.get("old_buyback_ask") is None:
                position.hold()
                return {"action": "HOLD", "date": date, "reason_code": "MISSING_EXACT_ROLL_QUOTE"}
            if (str(new_contract.get("symbol", "")).upper() != position.contract.symbol.upper()
                    or str(new_contract.get("quote_date", ""))[:10] != date[:10]
                    or str(new_contract.get("pit_status", "")) != "PIT_SAFE"):
                position.hold()
                return {"action": "HOLD", "date": date, "reason_code": "ROLL_QUOTE_IDENTITY_INVALID"}
            credit = position.roll_down_out(_contract(new_contract), float(observation["old_buyback_ask"]))
            return {"action": "ROLL", "date": date, "net_credit": credit,
                    "net_credit_per_share": credit, "net_credit_dollars": credit * 100,
                    "premium_received": float(new_contract["bid"]) * 100,
                    "premium_received_per_share": float(new_contract["bid"]),
                    "premium_received_dollars": float(new_contract["bid"]) * 100,
                    "buyback_cost": float(observation["old_buyback_ask"]) * 100,
                    "buyback_cost_per_share": float(observation["old_buyback_ask"]),
                    "buyback_cost_dollars": float(observation["old_buyback_ask"]) * 100,
                    "old_expiration": old_contract.expiration, "old_strike": old_contract.strike,
                    "old_quote_date": old_contract.quote_date, "old_close_ask": float(observation["old_buyback_ask"]),
                    "new_expiration": new_contract["expiration"], "new_strike": new_contract["strike"],
                    "new_quote_date": new_contract["quote_date"], "new_open_bid": float(new_contract["bid"])}
        if observation.get("buyback_ask") is not None:
            pnl = position.close(float(observation["buyback_ask"]))
            return {"action": position.state.value, "date": date, "pnl": pnl,
                    "premium_received": 0.0, "premium_received_per_share": 0.0, "premium_received_dollars": 0.0,
                    "buyback_cost": float(observation["buyback_ask"]) * 100,
                    "buyback_cost_per_share": float(observation["buyback_ask"]),
                    "buyback_cost_dollars": float(observation["buyback_ask"]) * 100}
        position.hold()
        return {"action": "HOLD", "date": date}

    def persist_result(self, result: CashSecuredPutRunResult, *, output_dir: str | Path,
                       candidates: list[Mapping[str, Any]] | None = None) -> CashSecuredPutRunResult:
        target = Path(output_dir) / self.spec.research_id
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{self.spec.research_id}.", dir=target.parent) as tmp:
            work = Path(tmp)
            injected = not isinstance(self.data_access, PCSDataAccess)
            if injected and "research_outputs" in str(output_dir).replace("\\", "/"):
                raise PermissionError("SYNTHETIC_TEST_FIXTURE_CANNOT_WRITE_RESEARCH_OUTPUTS")
            envelope = {"module": "pcs.research.cash_secured_put_runner", "strategy_type": self.spec.strategy_type.value,
                        "symbol": self.spec.ticker, "spec_hash": spec_hash(self.spec), "code_version": CALCULATION_VERSION,
                        "data_identity": "INJECTED_PIT_INPUTS" if injected else "PCS_DATA_ACCESS_VALIDATED",
                        "data_source": "INJECTED_PIT_INPUTS" if injected else "PCS_CANONICAL_DATA",
                        "run_id": os.urandom(8).hex(),
                        "request_id": os.urandom(8).hex(), "final_oos_read": False}
            (work / "readiness_report.json").write_text(json.dumps({**envelope, "status": result.status}, default=_json_default), encoding="utf-8")
            action_rows = [a for row in result.lifecycle_results for a in row.get("actions", [])]
            pd.DataFrame(action_rows).to_parquet(work / "daily_decision_packets.parquet", index=False)
            pd.DataFrame(candidates or [{"status": "NOT_PROVIDED", "reason_codes": ["CANDIDATES_NOT_PROVIDED"]}]).to_parquet(work / "candidate_contracts.parquet", index=False)
            selected = [{k: row.get(k) for k in ("episode_id", "entry_date", "original_entry_credit", "original_strike", "original_expiration", "original_collateral")} for row in result.lifecycle_results]
            pd.DataFrame(selected).to_parquet(work / "selected_trades.parquet", index=False)
            lifecycle_rows = [{k: v for k, v in row.items() if k != "actions"} for row in result.lifecycle_results]
            pd.DataFrame(lifecycle_rows).to_parquet(work / "lifecycle_results.parquet", index=False)
            pd.DataFrame(list(result.assignment_ledger)).to_parquet(work / "assignment_ledger.parquet", index=False)
            segment_columns = ["episode_id", "segment_id", "leg_number", "start_date", "end_date", "start_action", "end_action",
                               "strike", "expiration", "cumulative_net_credit_per_share", "collateral_required_dollars",
                               "calendar_days", "trading_days", "collateral_calendar_days", "collateral_trading_days",
                               "premium_received_dollars", "buyback_cost_dollars"]
            pd.DataFrame(list(result.collateral_segments), columns=segment_columns).to_parquet(work / "collateral_segments.parquet", index=False)
            gross = sum(float(x.get("gross_premium_received", 0)) for x in lifecycle_rows)
            assignment_mtm = sum(float(x.get("assignment_stock_component", 0)) for x in lifecycle_rows)
            metrics = {**envelope, "opened_puts": len(lifecycle_rows), "completed_positions": len(lifecycle_rows),
                       "gross_premium_received": gross, "total_buyback_cost": sum(float(x.get("cumulative_buyback_cost", 0)) for x in lifecycle_rows),
                       "net_option_pnl": sum(float(x.get("net_option_pnl", 0)) for x in lifecycle_rows),
                       "assignment_stock_component": assignment_mtm, "total_economic_pnl": sum(float(x.get("total_economic_pnl", 0)) for x in lifecycle_rows),
                       "pnl_per_put": None, "profit_factor": None, "max_drawdown": None,
                       "max_single_loss": None, "profit_close_count": sum(x.get("state") == "PROFIT_CLOSE" for x in lifecycle_rows),
                       "risk_close_count": sum(x.get("state") == "RISK_CLOSE" for x in lifecycle_rows),
                       "roll_count": sum(int(x.get("roll_count", 0)) for x in lifecycle_rows),
                       "expiry_worthless_count": sum(x.get("state") == "EXPIRE_WORTHLESS" for x in lifecycle_rows),
                       "assignment_count": len(result.assignment_ledger),
                       "average_holding_days": (sum(x.get("holding_calendar_days", 0) for x in lifecycle_rows) / len(lifecycle_rows) if lifecycle_rows else None),
                       "average_collateral": (sum(x.get("collateral_required", 0) for x in lifecycle_rows) / len(lifecycle_rows) if lifecycle_rows else None),
                       "peak_collateral": max((x.get("collateral_required", 0) for x in lifecycle_rows), default=None),
                       "collateral_days": sum(x.get("collateral_calendar_days", 0) for x in lifecycle_rows),
                       "yearly_pnl": {}, "reason_codes": ["METRICS_PARTIAL_UNREALIZED_FIELDS_NULL"]}
            for name in ("yearly_metrics.json", "strategy_comparison.json"):
                (work / name).write_text(json.dumps(metrics, default=_json_default), encoding="utf-8")
            files = [x for x in REQUIRED_ARTIFACTS if x != "artifact_manifest.json"]
            records = [{"path": x, "sha256": hashlib.sha256((work / x).read_bytes()).hexdigest()} for x in files]
            manifest = {**envelope, "research_id": self.spec.research_id, "current": True, "status": "CURRENT",
                        "data_source": envelope["data_source"], "files": records,
                        "created_at": datetime.now(timezone.utc).isoformat()}
            (work / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2, default=_json_default), encoding="utf-8")
            backup = target.with_name(target.name + ".replacement-backup")
            try:
                if backup.exists():
                    shutil.rmtree(backup)
                if target.exists():
                    os.replace(target, backup)
                os.replace(work, target)
            except Exception:
                if target.exists():
                    shutil.rmtree(target)
                if backup.exists():
                    os.replace(backup, target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        return CashSecuredPutRunResult(result.status, result.reason_codes, result.lifecycle_results, result.assignment_ledger, result.collateral_segments, str(target))
