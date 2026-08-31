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
        lifecycle: list[dict[str, Any]] = []
        assignments: list[dict[str, Any]] = []
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
            for observation in observations:
                state_before = position.state.value
                action = self._manage(position, observation, exact_quotes.get(identity, {}))
                actions.append({"episode_id": identity, "symbol": self.spec.ticker,
                                "state_before": state_before,
                                "state_after": position.state.value,
                                "expiration": position.contract.expiration, "strike": position.contract.strike,
                                "quote_date": position.contract.quote_date,
                                "bid": position.contract.bid, "ask": position.contract.ask,
                                "pit_status": position.contract.pit_status,
                                "collateral": position.collateral(),
                                "cumulative_premium": position.entry_credit * 100,
                                "cumulative_buyback_cost": action.get("buyback_cost", 0.0),
                                "realized_pnl": action.get("pnl"),
                                "reason_codes": [action.pop("reason_code")] if action.get("reason_code") else [],
                                **action, "episode_id": identity})
                if position.state in {PutLifecycleState.PROFIT_CLOSE, PutLifecycleState.RISK_CLOSE,
                                      PutLifecycleState.EXPIRE_WORTHLESS, PutLifecycleState.ASSIGNMENT}:
                    break
            lifecycle.append({"episode_id": identity, "state": position.state.value,
                              "roll_count": position.roll_count, "cumulative_credit": position.entry_credit,
                              "entry_date": original.quote_date, "original_entry_date": original.quote_date,
                              "original_strike": original.strike, "original_expiration": original.expiration,
                              "original_entry_credit": original_credit, "original_collateral": original_collateral,
                              "gross_premium": position.entry_credit * 100,
                              "collateral_required": original_collateral,
                              "holding_calendar_days": len(observations),
                              "collateral_calendar_days": position.collateral() * len(observations),
                              "actions": actions})
            if position.assignment:
                assignments.append({"episode_id": identity, **position.assignment.__dict__})
        result = CashSecuredPutRunResult("COMPLETED", ("CSP_LIFECYCLE_COMPLETED",), tuple(lifecycle), tuple(assignments))
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
            return {"action": position.state.value, "date": date, "pnl": value, "buyback_cost": 0.0}
        if observation.get("roll"):
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
            return {"action": "ROLL", "date": date, "net_credit": credit, "buyback_cost": float(observation["old_buyback_ask"]) * 100}
        if observation.get("buyback_ask") is not None:
            pnl = position.close(float(observation["buyback_ask"]))
            return {"action": position.state.value, "date": date, "pnl": pnl, "buyback_cost": float(observation["buyback_ask"]) * 100}
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
            selected = [{k: row.get(k) for k in ("episode_id", "entry_date", "gross_premium", "collateral_required")} for row in result.lifecycle_results]
            pd.DataFrame(selected).to_parquet(work / "selected_trades.parquet", index=False)
            lifecycle_rows = [{k: v for k, v in row.items() if k != "actions"} for row in result.lifecycle_results]
            pd.DataFrame(lifecycle_rows).to_parquet(work / "lifecycle_results.parquet", index=False)
            pd.DataFrame(list(result.assignment_ledger)).to_parquet(work / "assignment_ledger.parquet", index=False)
            gross = sum(float(x.get("gross_premium", 0)) for x in lifecycle_rows)
            assignment_mtm = sum(float(x.get("stock_mtm", 0)) for x in result.assignment_ledger)
            metrics = {**envelope, "opened_puts": len(lifecycle_rows), "completed_positions": len(lifecycle_rows),
                       "gross_premium": gross, "buyback_cost": None, "net_option_pnl": None,
                       "assignment_stock_mtm": assignment_mtm, "total_economic_pnl": assignment_mtm,
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
        return CashSecuredPutRunResult(result.status, result.reason_codes, result.lifecycle_results, result.assignment_ledger, str(target))
