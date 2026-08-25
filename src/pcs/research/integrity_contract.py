"""Small, typed contracts for reproducible PCS research execution.

The module is deliberately independent of pandas and strategy code.  It gives
the orchestration boundary stable identities and prevents a candidate ledger
from being passed to lifecycle execution by accident.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence


class LedgerKind(str, Enum):
    CANDIDATE = "CANDIDATE_LEDGER"
    SELECTED_TRADE = "SELECTED_TRADE_LEDGER"
    LIFECYCLE = "LIFECYCLE_LEDGER"


class IntegrityError(ValueError):
    """A research boundary was violated."""


def deterministic_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerContract:
    kind: LedgerKind
    rows: tuple[Mapping[str, Any], ...]
    symbol: str
    run_id: str
    ledger_hash: str

    @classmethod
    def build(cls, kind: LedgerKind, rows: Sequence[Mapping[str, Any]], *, symbol: str, run_id: str) -> "LedgerContract":
        frozen = tuple(dict(row) for row in rows)
        return cls(kind, frozen, str(symbol).upper(), run_id, deterministic_hash({"kind": kind.value, "rows": frozen}))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "kind": self.kind.value, "rows": [dict(row) for row in self.rows]}


CandidateLedger = LedgerContract
SelectedTradeLedger = LedgerContract
LifecycleLedger = LedgerContract


def require_kind(ledger: LedgerContract, expected: LedgerKind) -> LedgerContract:
    if not isinstance(ledger, LedgerContract) or ledger.kind is not expected:
        actual = getattr(getattr(ledger, "kind", None), "value", type(ledger).__name__)
        raise IntegrityError(f"INVALID_LEDGER_BOUNDARY:expected={expected.value}:actual={actual}")
    return ledger


def validate_execution_cardinality(*, signal_count: int, episode_count: int,
                                   selected: LedgerContract, lifecycle: LedgerContract,
                                   one_entry_per_episode: bool = True) -> None:
    require_kind(selected, LedgerKind.SELECTED_TRADE)
    require_kind(lifecycle, LedgerKind.LIFECYCLE)
    if len(selected.rows) > signal_count or len(selected.rows) > episode_count:
        raise IntegrityError("SELECTED_TRADE_CARDINALITY_EXCEEDED")
    if len(lifecycle.rows) > len(selected.rows):
        raise IntegrityError("LIFECYCLE_CARDINALITY_EXCEEDED")
    if one_entry_per_episode and len(selected.rows) > episode_count:
        raise IntegrityError("ONE_ENTRY_PER_EPISODE_VIOLATION")


REPRODUCIBILITY_REQUIRED = (
    "git_commit_sha", "research_spec_hash", "strategy_definition_hash",
    "runner_version", "feature_calculation_version", "daily_source_version",
    "options_source_version", "daily_manifest_path", "options_manifest_path",
    "daily_manifest_sha", "options_manifest_sha",
    "corporate_action_version", "config_hash", "population_hash",
    "candidates_ledger_hash", "selected_trade_ledger_hash", "lifecycle_ledger_hash",
)


def validate_reproducibility_manifest(manifest: Mapping[str, Any]) -> None:
    missing = [key for key in REPRODUCIBILITY_REQUIRED if not manifest.get(key)]
    if missing:
        raise IntegrityError("LEGACY_REFERENCE_INCOMPLETE:" + ",".join(missing))
