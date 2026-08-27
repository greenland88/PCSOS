"""Versioned support-state semantics for Entry Contract v2."""
from __future__ import annotations

from enum import StrEnum
from typing import Any
import pandas as pd


class SupportState(StrEnum):
    SUPPORT_FOUND = "SUPPORT_FOUND"
    NO_SUPPORT = "NO_SUPPORT"
    SUPPORT_DATA_MISSING = "SUPPORT_DATA_MISSING"


SUPPORT_PRODUCER_VERSION = "pcs.trend.support.analyze_support:v1"


def classify_support(payload: dict[str, Any]) -> tuple[SupportState, float | None, str]:
    if not payload.get("available", False):
        return SupportState.SUPPORT_DATA_MISSING, None, "support_producer_unavailable"
    nearest = payload.get("nearest_support")
    if nearest is None:
        return SupportState.NO_SUPPORT, None, "no_nearby_support"
    return SupportState.SUPPORT_FOUND, float(nearest), "nearest_valid_support"


def support_contract_valid(row: dict[str, Any]) -> bool:
    state = str(row.get("support_state", ""))
    level = row.get("support_level")
    numeric = level is not None and not pd.isna(level)
    return (state == SupportState.SUPPORT_FOUND and numeric) or (state == SupportState.NO_SUPPORT and not numeric)
