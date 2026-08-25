"""Research-only Phase 0 replay contracts and validation.

This module is intentionally an infrastructure boundary: it does not select
trades, alter production rules, or run a Phase 1 comparison.  It provides
stable records for the complete candidate universe, daily spread marks,
point-in-time structure state, deterministic strike mapping, and coverage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from pcs.data.access import PCSDataAccess


ACTION_FIELDS = {"OPEN", "WAIT", "HOLD", "CLOSE", "ROLL"}
UNIVERSE_COLUMNS = (
    "ticker", "decision_date", "candidate_id", "candidate_status", "expiration",
    "short_strike", "long_strike", "chain_available", "liquidity_valid",
    "event_data_valid", "safe_strike_target", "safe_strike_available",
)

LIFECYCLE_COLUMNS = (
    "ticker", "candidate_id", "mark_date", "expiration", "short_strike",
    "long_strike", "short_bid", "short_ask", "long_bid", "long_ask",
    "spread_mark", "quote_available", "contract_match", "is_expiration",
)

def resolve_canonical_source(ticker: str, manifest_path: str | Path = "data/manifests/storage_manifest.csv",
                             parquet_root: str | Path = "data/parquet/options") -> dict[str, Any]:
    """Resolve ticker-specific Parquet partitions and manifest provenance."""
    # The default call must use the active per-ticker route.  Constructing an
    # explicit access object from the legacy options path disables routing and
    # can make Phase 0 inspect a different source than the replay engine.
    default_call = (str(manifest_path).replace("\\", "/") == "data/manifests/storage_manifest.csv"
                    and str(parquet_root).replace("\\", "/") == "data/parquet/options")
    access = PCSDataAccess() if default_call else PCSDataAccess(
        manifest_path=manifest_path, parquet_root=Path(parquet_root).parent)
    spec = access.resolve_source("options", ticker)
    provenance = access.get_provenance("options", ticker)
    resolved_manifest = str(spec.source_version).split(":", 1)[1] if ":" in str(spec.source_version) else str(manifest_path)
    return {"ticker": spec.symbol, "backend": spec.backend, "glob": spec.path,
            "min_date": spec.first_date, "max_date": spec.last_date,
            "imported_row_count": spec.row_count,
            "expected_partitions": [{"year": r.get("year"), "quarter": r.get("quarter")} for r in provenance],
            "manifest_path": resolved_manifest, "source_version": spec.source_version,
            "schema_version": spec.schema_version}


@dataclass(frozen=True)
class Phase0Coverage:
    ticker: str
    method: str
    total_candidate_dates: int
    valid_option_chain_pct: float
    complete_universe_pct: float
    valid_lifecycle_marks_pct: float
    support_state_pct: float
    trend_gate_pct: float
    event_data_pct: float
    fully_replayable_pct: float
    missing_data_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_candidate_universe(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a stable, typed universe frame without dropping rejected rows.

    This is a storage boundary only: it never applies eligibility rules or
    fills missing values.  Missing fields remain explicit nulls.
    """
    out = frame.copy()
    for col in UNIVERSE_COLUMNS:
        if col not in out:
            out[col] = pd.NA
    out["decision_date"] = pd.to_datetime(out["decision_date"], errors="coerce").dt.normalize()
    out["expiration"] = pd.to_datetime(out["expiration"], errors="coerce").dt.normalize()
    for col in ("chain_available", "liquidity_valid", "event_data_valid", "safe_strike_available"):
        out[col] = out[col].astype("boolean")
    return out.loc[:, list(UNIVERSE_COLUMNS) + [c for c in out.columns if c not in UNIVERSE_COLUMNS]]


def validate_lifecycle(frame: pd.DataFrame) -> list[str]:
    """Validate lifecycle rows for contract alignment and explicit missing quotes."""
    failures: list[str] = []
    required = {"mark_date", "expiration", "short_strike", "long_strike"}
    if not required.issubset(frame.columns):
        return ["LIFECYCLE_SCHEMA_INCOMPLETE"]
    mark = pd.to_datetime(frame["mark_date"], errors="coerce")
    expiry = pd.to_datetime(frame["expiration"], errors="coerce")
    if mark.isna().any() or expiry.isna().any(): failures.append("INVALID_LIFECYCLE_DATE")
    if (mark > expiry).fillna(False).any(): failures.append("MARK_AFTER_EXPIRATION")
    if (pd.to_numeric(frame["long_strike"], errors="coerce") >= pd.to_numeric(frame["short_strike"], errors="coerce")).fillna(False).any():
        failures.append("INVALID_STRIKE_ORDER")
    if "quote_available" in frame and frame["quote_available"].isna().any(): failures.append("UNMARKED_MISSING_QUOTE")
    return sorted(set(failures))


def persist_phase0_artifacts(universe: pd.DataFrame, lifecycle: pd.DataFrame,
                             output_dir: str | Path) -> dict[str, str]:
    """Persist replay inputs as deterministic Parquet artifacts and return paths."""
    root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
    access = PCSDataAccess(manifest_path=root / ".storage_manifest.csv", parquet_root=root.parent)
    paths = {"candidate_universe": access.write_artifact(normalize_candidate_universe(universe), "phase0", "candidate_universe.parquet", root=root),
             "lifecycle_marks": access.write_artifact(lifecycle.reindex(columns=list(LIFECYCLE_COLUMNS) + [c for c in lifecycle.columns if c not in LIFECYCLE_COLUMNS]), "phase0", "lifecycle_marks.parquet", root=root)}
    return {k: str(v) for k, v in paths.items()}


def write_coverage_report(coverages: Iterable[Phase0Coverage], path: str | Path) -> str:
    """Write machine-readable coverage, without triggering Phase 1."""
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([c.to_dict() for c in coverages]).to_json(target, orient="records", indent=2, date_format="iso")
    return str(target)


def map_target_to_listed_strike(target_price: float, listed_strikes: Iterable[float],
                                convention: str = "nearest_at_or_below") -> float | None:
    """Map a historical price target to a real listed strike.

    The convention is explicit and must be supplied by the research manifest;
    this function never creates synthetic strikes or substitutes a threshold.
    """
    strikes = sorted({float(x) for x in listed_strikes if pd.notna(x)})
    if not strikes:
        return None
    if convention == "nearest_at_or_below":
        eligible = [x for x in strikes if x <= float(target_price)]
        return max(eligible) if eligible else None
    if convention == "nearest":
        return min(strikes, key=lambda x: (abs(x - float(target_price)), x))
    if convention == "nearest_at_or_above":
        eligible = [x for x in strikes if x >= float(target_price)]
        return min(eligible) if eligible else None
    raise ValueError(f"unsupported strike mapping convention: {convention}")


def validate_point_in_time(frame: pd.DataFrame, decision_col: str = "decision_timestamp",
                           source_col: str = "chain_timestamp") -> list[str]:
    """Return machine-readable validation failures; no repair or imputation occurs."""
    failures: list[str] = []
    if decision_col in frame and source_col in frame:
        d, s = pd.to_datetime(frame[decision_col], errors="coerce"), pd.to_datetime(frame[source_col], errors="coerce")
        if d.isna().any(): failures.append("INVALID_DECISION_TIMESTAMP")
        if s.isna().any(): failures.append("INVALID_CHAIN_TIMESTAMP")
        if (s > d).fillna(False).any(): failures.append("FUTURE_CHAIN_DATA")
    for col in ("support_asof", "trend_asof", "event_asof"):
        if col in frame and "decision_date" in frame:
            if (pd.to_datetime(frame[col], errors="coerce") > pd.to_datetime(frame["decision_date"], errors="coerce")).fillna(False).any():
                failures.append(f"FUTURE_{col.upper()}")
    return sorted(set(failures))


def build_coverage(frame: pd.DataFrame, ticker: str, method: str) -> Phase0Coverage:
    """Calculate coverage from persisted rows, preserving rejected rows."""
    n = len(frame)
    def pct(col: str) -> float:
        return round(float(frame[col].fillna(False).astype(bool).mean() * 100), 4) if n and col in frame else 0.0
    complete = frame.get("complete_universe", pd.Series(False, index=frame.index))
    full = frame.get("fully_replayable", pd.Series(False, index=frame.index))
    reasons = sorted({str(x) for values in frame.get("missing_data_reasons", []) for x in (values if isinstance(values, (list, tuple)) else str(values).split("|")) if x and x != "nan"})
    return Phase0Coverage(ticker, method, n, pct("chain_available"), round(float(complete.astype(bool).mean()*100), 4) if n else 0.0,
                          pct("lifecycle_complete"), pct("support_available"), pct("trend_gate_available"), pct("event_data_valid"),
                          round(float(full.astype(bool).mean()*100), 4) if n else 0.0, tuple(reasons))
