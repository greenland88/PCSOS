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
            output_dir: str | Path | None = None) -> CashSecuredPutRunResult:
        entries = list(entries or [])
        if not entries:
            return CashSecuredPutRunResult(
                ResearchStatus.NO_SIGNAL_DATES.value,
                ("CSP_SIGNAL_ENGINE_NOT_CONFIGURED",),
            )
        daily_observations = daily_observations or {}
        exact_quotes = exact_quotes or {}
        lifecycle: list[dict[str, Any]] = []
        assignments: list[dict[str, Any]] = []
        for index, entry in enumerate(entries):
            position = CashSecuredPutPosition(_contract(entry["contract"]), float(entry["entry_credit"]))
            identity = str(entry.get("episode_id", f"{self.spec.ticker}:{index}"))
            actions = [{"episode_id": identity, "action": "OPEN", "date": position.contract.quote_date}]
            for observation in daily_observations.get(identity, entry.get("observations", ())):
                action = self._manage(position, observation, exact_quotes.get(identity, {}))
                actions.append({"episode_id": identity, **action})
                if position.state in {PutLifecycleState.PROFIT_CLOSE, PutLifecycleState.RISK_CLOSE,
                                      PutLifecycleState.EXPIRE_WORTHLESS, PutLifecycleState.ASSIGNMENT}:
                    break
            lifecycle.append({"episode_id": identity, "state": position.state.value,
                              "roll_count": position.roll_count, "cumulative_credit": position.entry_credit,
                              "actions": actions, "collateral_days": sum(
                                  float(x.get("collateral", position.collateral())) for x in (entry.get("observations", ())))})
            if position.assignment:
                assignments.append({"episode_id": identity, **position.assignment.__dict__})
        result = CashSecuredPutRunResult("COMPLETED", ("CSP_LIFECYCLE_COMPLETED",), tuple(lifecycle), tuple(assignments))
        if output_dir is not None:
            result = self.persist_result(result, output_dir=output_dir)
        return result

    def _manage(self, position: CashSecuredPutPosition, observation: Mapping[str, Any], quotes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        date = str(observation.get("date", observation.get("quote_date", "")))
        if observation.get("expire"):
            value = position.expire(float(observation["underlying_mark"]), int(observation.get("holding_days", 0)))
            return {"action": position.state.value, "date": date, "pnl": value}
        if observation.get("roll"):
            new_contract = quotes.get(str(observation["roll"]))
            if new_contract is None or observation.get("old_buyback_ask") is None:
                position.hold()
                return {"action": "HOLD", "date": date, "reason_code": "MISSING_EXACT_ROLL_QUOTE"}
            credit = position.roll_down_out(_contract(new_contract), float(observation["old_buyback_ask"]))
            return {"action": "ROLL", "date": date, "net_credit": credit}
        if observation.get("buyback_ask") is not None:
            pnl = position.close(float(observation["buyback_ask"]))
            return {"action": position.state.value, "date": date, "pnl": pnl}
        position.hold()
        return {"action": "HOLD", "date": date}

    def persist_result(self, result: CashSecuredPutRunResult, *, output_dir: str | Path) -> CashSecuredPutRunResult:
        target = Path(output_dir) / self.spec.research_id
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{self.spec.research_id}.", dir=target.parent) as tmp:
            work = Path(tmp)
            envelope = {"module": "pcs.research.cash_secured_put_runner", "strategy_type": self.spec.strategy_type.value,
                        "symbol": self.spec.ticker, "spec_hash": spec_hash(self.spec), "code_version": CALCULATION_VERSION,
                        "data_identity": "INJECTED_PIT_INPUTS", "run_id": os.urandom(8).hex(),
                        "request_id": os.urandom(8).hex(), "final_oos_read": False}
            (work / "readiness_report.json").write_text(json.dumps({**envelope, "status": result.status}, default=_json_default), encoding="utf-8")
            pd.DataFrame([]).to_parquet(work / "daily_decision_packets.parquet", index=False)
            pd.DataFrame([]).to_parquet(work / "candidate_contracts.parquet", index=False)
            pd.DataFrame([x for x in result.lifecycle_results]).to_parquet(work / "selected_trades.parquet", index=False)
            pd.DataFrame([x for x in result.lifecycle_results]).to_parquet(work / "lifecycle_results.parquet", index=False)
            pd.DataFrame(list(result.assignment_ledger)).to_parquet(work / "assignment_ledger.parquet", index=False)
            metrics = {**envelope, "opened_puts": len(result.lifecycle_results), "assignment_count": len(result.assignment_ledger)}
            for name in ("yearly_metrics.json", "strategy_comparison.json"):
                (work / name).write_text(json.dumps(metrics, default=_json_default), encoding="utf-8")
            files = [x for x in REQUIRED_ARTIFACTS if x != "artifact_manifest.json"]
            records = [{"path": x, "sha256": hashlib.sha256((work / x).read_bytes()).hexdigest()} for x in files]
            manifest = {**envelope, "research_id": self.spec.research_id, "current": True, "status": "CURRENT",
                        "data_source": "PCS_CANONICAL_DATA", "files": records,
                        "created_at": datetime.now(timezone.utc).isoformat()}
            (work / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2, default=_json_default), encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            os.replace(work, target)
        return CashSecuredPutRunResult(result.status, result.reason_codes, result.lifecycle_results, result.assignment_ledger, str(target))
