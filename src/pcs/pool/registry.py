"""Explicit universe registry and static eligibility evaluation."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping, Sequence
import json
import pandas as pd
import yaml

from pcs.data.universe import load_market_universe, merge_symbols
from .models import EligibilityStatus


@dataclass(frozen=True)
class UniverseSpec:
    universe_id: str
    symbols: tuple[str, ...]
    version: str = "1"

    @classmethod
    def from_symbols(cls, symbols: Sequence[str], universe_id: str = "explicit", version: str = "1"):
        normalized = merge_symbols(explicit_symbols=symbols)
        if not normalized:
            raise ValueError("universe must contain at least one symbol")
        return cls(universe_id, tuple(normalized), version)

    @classmethod
    def from_config(cls, path: str | Path, groups: Sequence[str] = ("pcs_universe",)):
        return cls.from_symbols(load_market_universe(groups=groups, path=Path(path)), universe_id=str(path))

    @classmethod
    def from_file(cls, path: str | Path, *, symbol_column: str = "symbol"):
        """Import an explicit symbol list without assuming a repository filename."""
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
            values = payload.get(symbol_column, payload) if isinstance(payload, dict) else payload
        elif suffix == ".json":
            payload = json.loads(source.read_text(encoding="utf-8"))
            values = payload.get(symbol_column, payload) if isinstance(payload, dict) else payload
        elif suffix in {".csv", ".tsv"}:
            frame = pd.read_csv(source, sep="\t" if suffix == ".tsv" else ",")
            if symbol_column not in frame.columns:
                raise ValueError(f"universe file missing column: {symbol_column}")
            values = frame[symbol_column].tolist()
        else:
            raise ValueError("universe file must be CSV, TSV, JSON, YAML, or YML")
        if not isinstance(values, (list, tuple)):
            raise ValueError("universe file must contain a symbol list")
        return cls.from_symbols(values, universe_id=str(source.resolve()))


@dataclass(frozen=True)
class RegistryEntry:
    symbol: str
    status: EligibilityStatus
    reason_codes: tuple[str, ...]
    next_review_at: str | None = None
    reentry_conditions: tuple[str, ...] = ()


def evaluate_static_eligibility(symbol: str, metadata: Mapping[str, Any] | None = None) -> RegistryEntry:
    """Evaluate only stable/static facts; absence of metadata fails closed."""
    normalized = str(symbol).strip().upper()
    if not normalized:
        return RegistryEntry("", EligibilityStatus.HARD_EXCLUDED, ("SYMBOL_EMPTY",))
    meta = dict(metadata or {})
    reasons: list[str] = []
    if meta.get("security_type") not in (None, "EQUITY", "ETF"):
        reasons.append("UNSUPPORTED_SECURITY_TYPE")
    if meta.get("listed") is False or meta.get("tradable") is False:
        reasons.append("SECURITY_NOT_TRADABLE")
    if meta.get("optionable") is False:
        reasons.append("OPTIONS_NOT_LISTED")
    if meta.get("canonical_daily") is False:
        reasons.append("CANONICAL_DAILY_UNAVAILABLE")
    if meta.get("warmup_ready") is False:
        reasons.append("DAILY_WARMUP_INSUFFICIENT")
    if meta.get("static_liquidity_ok") is False:
        reasons.append("STATIC_LIQUIDITY_CONTRACT_FAILED")
    if reasons:
        permanent = any(code in reasons for code in ("UNSUPPORTED_SECURITY_TYPE", "SECURITY_NOT_TRADABLE", "OPTIONS_NOT_LISTED"))
        status = EligibilityStatus.HARD_EXCLUDED if permanent else EligibilityStatus.DATA_BLOCKED
        return RegistryEntry(normalized, status, tuple(reasons), meta.get("next_review_at"))
    return RegistryEntry(normalized, EligibilityStatus.PCS_ELIGIBLE, ("STATIC_ELIGIBILITY_PASS",))


def persist_registry(entries: Sequence[RegistryEntry], path: str | Path) -> None:
    """Persist a complete registry snapshot using a temporary file replacement."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps([asdict(entry) for entry in entries], default=str, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(target)
