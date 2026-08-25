"""Auditable, fail-closed Stage 4A production decision evaluation.

This module owns the *orchestration boundary* between a frozen structural
opportunity population and :class:`pcs.engine.decision_engine.DecisionEngine`.
It deliberately does not create a market-regime substitute, alter a strategy
rule, or read option parquet files directly.  Every option-chain read is made
through ``PCSDataAccess`` and a partition is reusable only when its completion
receipt proves identity parity with its source population.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
import json
import os
import uuid

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.entry.contract_v2 import later_expirations, nearby_strikes


MODULE = "pcs.research.stage4a_production_evaluation"
VERSION = "stage4a-production-evaluation-v2"


class DecisionRowStatus(StrEnum):
    EVALUATED_ACCEPTED = "EVALUATED_ACCEPTED"
    EVALUATED_REJECTED = "EVALUATED_REJECTED"
    BLOCKED_SOURCE_UNAVAILABLE = "BLOCKED_SOURCE_UNAVAILABLE"
    BLOCKED_CONTEXT_UNAVAILABLE = "BLOCKED_CONTEXT_UNAVAILABLE"
    BLOCKED_SUPPORT_UNAVAILABLE = "BLOCKED_SUPPORT_UNAVAILABLE"
    BLOCKED_PIT_FAILURE = "BLOCKED_PIT_FAILURE"
    BLOCKED_OTHER = "BLOCKED_OTHER"


class Stage4AEvaluationError(RuntimeError):
    """Raised when a partition cannot meet the Stage 4A audit contract."""


def _identity_digest(values: pd.Series) -> str:
    return sha256("\n".join(sorted(values.astype(str))).encode("utf-8")).hexdigest()


def _frame_digest(frame: pd.DataFrame) -> str:
    normalized = frame.sort_index(axis=1).sort_values(list(frame.columns), kind="mergesort").reset_index(drop=True)
    return sha256(pd.util.hash_pandas_object(normalized, index=True).values.tobytes()).hexdigest()


@dataclass(frozen=True)
class PartitionReceipt:
    """Stable completion proof for one source-partition evaluation."""

    module: str
    version: str
    status: str
    run_id: str
    request_id: str
    source_partition: str
    source_rows: int
    source_identity_sha256: str
    result_rows: int
    result_identity_sha256: str
    data_timestamp: str
    calculation_version: str
    reason_codes: tuple[str, ...] = ()
    source_content_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"reason_codes": list(self.reason_codes)}


def atomic_parquet(frame: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(tmp, index=False)
        verified = pd.read_parquet(tmp)
        if len(verified) != len(frame):
            raise Stage4AEvaluationError("PARQUET_ROW_COUNT_VERIFICATION_FAILED")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_json(value: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def validate_source_partition(frame: pd.DataFrame) -> None:
    if "opportunity_id" not in frame:
        raise Stage4AEvaluationError("SOURCE_OPPORTUNITY_ID_MISSING")
    if frame.opportunity_id.isna().any() or frame.opportunity_id.astype(str).eq("").any():
        raise Stage4AEvaluationError("SOURCE_OPPORTUNITY_ID_INVALID")
    if frame.opportunity_id.duplicated().any():
        raise Stage4AEvaluationError("SOURCE_OPPORTUNITY_ID_DUPLICATE")


def receipt_path(result_path: str | Path) -> Path:
    result = Path(result_path)
    return result.with_suffix(".receipt.json")


def completion_is_valid(source: pd.DataFrame, result_path: str | Path, *, calculation_version: str | None = None) -> bool:
    """Return true only for an identity-complete, explicitly finished output.

    A zero-row source is a valid completed unit, but only if it has a matching
    receipt.  File existence alone is intentionally never a completion signal.
    """
    validate_source_partition(source)
    result_path = Path(result_path)
    marker = receipt_path(result_path)
    if not result_path.exists() or not marker.exists():
        return False
    try:
        receipt = json.loads(marker.read_text(encoding="utf-8"))
        result = pd.read_parquet(result_path)
        if "opportunity_id" not in result or "status" not in result:
            return False
        return bool(
            receipt.get("status") == "COMPLETE"
            and (calculation_version is None or receipt.get("calculation_version") == calculation_version)
            and receipt.get("source_rows") == len(source)
            and receipt.get("result_rows") == len(result)
            and receipt.get("source_identity_sha256") == _identity_digest(source.opportunity_id)
            and receipt.get("source_content_sha256") == _frame_digest(source)
            and receipt.get("result_identity_sha256") == _identity_digest(result.opportunity_id)
            and len(result) == len(source)
            and set(result.opportunity_id.astype(str)) == set(source.opportunity_id.astype(str))
            and result.status.astype(str).isin({x.value for x in DecisionRowStatus}).all()
        )
    except (OSError, ValueError, KeyError, pd.errors.ParserError):
        return False


def write_completed_partition(
    source: pd.DataFrame,
    result: pd.DataFrame,
    result_path: str | Path,
    *,
    source_partition: str,
    run_id: str,
    request_id: str,
    data_timestamp: str,
    calculation_version: str = VERSION,
) -> PartitionReceipt:
    """Persist a result and its receipt after strict source/result parity checks."""
    validate_source_partition(source)
    if "opportunity_id" not in result or "status" not in result:
        raise Stage4AEvaluationError("RESULT_REQUIRED_FIELDS_MISSING")
    if len(result) != len(source) or result.opportunity_id.duplicated().any():
        raise Stage4AEvaluationError("RESULT_ROW_OR_IDENTITY_PARITY_FAILED")
    if set(result.opportunity_id.astype(str)) != set(source.opportunity_id.astype(str)):
        raise Stage4AEvaluationError("RESULT_OPPORTUNITY_IDENTITY_PARITY_FAILED")
    if not result.status.astype(str).isin({x.value for x in DecisionRowStatus}).all():
        raise Stage4AEvaluationError("RESULT_STATUS_NOT_NORMALIZED")
    result_path = Path(result_path)
    atomic_parquet(result, result_path)
    receipt = PartitionReceipt(
        module=MODULE, version=VERSION, status="COMPLETE", run_id=run_id,
        request_id=request_id, source_partition=source_partition,
        source_rows=len(source), source_identity_sha256=_identity_digest(source.opportunity_id),
        result_rows=len(result), result_identity_sha256=_identity_digest(result.opportunity_id),
        data_timestamp=data_timestamp, calculation_version=calculation_version,
        source_content_sha256=_frame_digest(source),
    )
    atomic_json(receipt.to_dict(), receipt_path(result_path))
    return receipt


def canonical_breadth(access: PCSDataAccess, symbol: str, trade_date, expiration, short_strike: float) -> tuple[int, int, dict[str, str]]:
    """Calculate v2 breadth from the complete canonical same-day PIT chain."""
    chain = access.read_option_chain(symbol, trade_date)
    if chain.empty:
        raise Stage4AEvaluationError("PIT_OPTION_CHAIN_EMPTY")
    # Contract-v2 accepts the canonical schema directly; no opportunity rows are
    # used to infer a missing strike or expiration.
    return (
        nearby_strikes(chain, expiration, "p", short_strike),
        later_expirations(chain, expiration, "p"),
        {"source": "PCSDataAccess.read_option_chain", "symbol": str(symbol).upper(), "trade_date": str(pd.Timestamp(trade_date).date())},
    )


def evaluate_partition(
    source: pd.DataFrame,
    row_evaluator: Callable[[dict[str, Any]], dict[str, Any]],
) -> pd.DataFrame:
    """Evaluate every identity once; unexpected failures remain explicit blocks."""
    validate_source_partition(source)
    if source.empty:
        # Preserve the result schema for a legitimate zero-opportunity source.
        out = source.copy()
        out["status"] = pd.Series(dtype="string")
        out["accepted"] = pd.Series(dtype="bool")
        out["reason_codes"] = pd.Series(dtype="object")
        out["primary_reason"] = pd.Series(dtype="string")
        return out
    output: list[dict[str, Any]] = []
    for row in source.to_dict("records"):
        try:
            evaluated = row_evaluator(row)
            if evaluated.get("opportunity_id") != row["opportunity_id"]:
                raise Stage4AEvaluationError("EVALUATOR_IDENTITY_MUTATION")
            if str(evaluated.get("status")) not in {x.value for x in DecisionRowStatus}:
                raise Stage4AEvaluationError("EVALUATOR_STATUS_NOT_NORMALIZED")
            output.append(evaluated)
        except Exception as exc:  # fail closed, retaining the exact opportunity identity
            output.append({
                **row,
                "status": DecisionRowStatus.BLOCKED_OTHER.value,
                "accepted": False,
                "reason_codes": ["UNEXPECTED_EVALUATION_FAILURE", type(exc).__name__],
                "primary_reason": "UNEXPECTED_EVALUATION_FAILURE",
            })
    return pd.DataFrame(output)
