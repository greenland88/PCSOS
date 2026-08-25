"""Deterministic identity for PIT feature timelines.

Only upstream PIT inputs are included. Outcomes, lifecycle/PnL, validation,
and FINAL OOS artifacts are intentionally excluded.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PRICE_BASIS_VERSION = "price_basis.v1"
CORPORATE_ACTION_REGISTRY_VERSION = "corporate_actions.registry.v1"
PIT_FEATURE_IMPLEMENTATION_VERSION = "pit_features.v2"
PIT_CONTEXT_SCHEMA_VERSION = "pit_context.schema.v1"

def file_digest(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return "MISSING"
    return hashlib.sha256(p.read_bytes()).hexdigest()

def build_pit_cache_identity(*, symbol: str, date_range: dict[str, str], daily_data_identity: str,
                             feature_config: Any, research_config: Any = None,
                             corporate_action_path: str | Path = "config/data/corporate_actions.csv") -> dict[str, Any]:
    config_hash = hashlib.sha256(json.dumps(feature_config, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    research_hash = hashlib.sha256(json.dumps(research_config or {}, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    action_digest = file_digest(corporate_action_path)
    identity = {
        "symbol": str(symbol).upper(), "date_range_start": str(date_range["start"]), "date_range_end": str(date_range["end"]),
        "daily_data_identity": str(daily_data_identity), "price_basis_version": PRICE_BASIS_VERSION,
        "corporate_action_registry_version": CORPORATE_ACTION_REGISTRY_VERSION,
        "corporate_action_registry_digest": action_digest,
        "feature_implementation_version": PIT_FEATURE_IMPLEMENTATION_VERSION,
        "feature_config_hash": config_hash, "research_config_hash": research_hash,
        "pit_context_schema_version": PIT_CONTEXT_SCHEMA_VERSION,
    }
    identity["identity_sha256"] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return identity

def cache_identity_matches(frame, identity: dict[str, Any]) -> bool:
    required = set(identity)
    return required.issubset(frame.columns) and all(frame[k].astype(str).eq(str(v)).all() for k, v in identity.items())
