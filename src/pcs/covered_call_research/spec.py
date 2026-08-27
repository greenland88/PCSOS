"""Frozen, non-PCS specification for long-stock covered-call research."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json

import yaml


@dataclass(frozen=True)
class CoveredCallResearchSpec:
    spec_id: str
    symbol: str
    status: str
    data_source: str
    position_model: dict[str, Any]
    execution: dict[str, Any]
    parameters: dict[str, Any]
    data_split: dict[str, Any]
    metrics: tuple[str, ...]
    final_oos_access: bool = False

    @property
    def spec_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id, "symbol": self.symbol, "status": self.status,
            "data_source": self.data_source, "position_model": self.position_model,
            "execution": self.execution, "parameters": self.parameters,
            "data_split": self.data_split, "metrics": list(self.metrics),
            "final_oos_access": self.final_oos_access,
        }

    def validate(self) -> None:
        if self.status != "FROZEN": raise ValueError("SPEC_NOT_FROZEN")
        if self.data_source != "PCS_CANONICAL_DATA": raise ValueError("NON_CANONICAL_DATA_SOURCE")
        if self.final_oos_access: raise ValueError("FINAL_OOS_MUST_REMAIN_SEALED")
        if self.position_model.get("shares_per_call") != 100: raise ValueError("SHARES_PER_CALL_CHANGED")
        if self.position_model.get("max_short_calls") != 3: raise ValueError("MAX_CALL_CAPACITY_CHANGED")
        if self.execution.get("entry_price") != "bid" or self.execution.get("buyback_price") != "ask":
            raise ValueError("NON_EXECUTABLE_PRICE_POLICY")
        if self.execution.get("roll_requires_same_day_buyback_and_sale") is not True:
            raise ValueError("ROLL_MUST_BE_SAME_DAY_TWO_LEG")
        if self.execution.get("year_end_forced_close") is not False:
            raise ValueError("YEAR_END_FORCE_CLOSE_FORBIDDEN")


def load_spec(path: str | Path) -> CoveredCallResearchSpec:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    spec = CoveredCallResearchSpec(
        spec_id=raw["spec_id"], symbol=str(raw["symbol"]).upper(), status=raw["status"],
        data_source=raw["data_source"], position_model=raw["position_model"],
        execution=raw["execution"], parameters=raw["parameters"],
        data_split=raw["data_split"], metrics=tuple(raw["metrics"]),
        final_oos_access=bool(raw.get("final_oos_access", False)),
    )
    spec.validate()
    return spec
