"""Explicit universe registry and static eligibility evaluation."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping, Sequence
import json
import hashlib
import os
import pandas as pd
import yaml

from pcs.data.universe import load_market_universe, merge_symbols
from .models import EligibilityStatus


@dataclass(frozen=True)
class UniverseSpec:
    universe_id: str
    symbols: tuple[str, ...]
    version: str = "1"
    universe_role: str = "EXPLICIT"
    fingerprint: str = ""

    @classmethod
    def from_symbols(cls, symbols: Sequence[str], universe_id: str = "explicit", version: str = "1"):
        normalized = merge_symbols(explicit_symbols=symbols)
        if not normalized:
            raise ValueError("universe must contain at least one symbol")
        role = "CORE_WATCHLIST" if universe_id in {"pcs_universe", "core_watchlist"} else "EXPLICIT"
        return cls(universe_id, tuple(normalized), version, role)

    @classmethod
    def from_config(cls, path: str | Path, groups: Sequence[str] = ("pcs_universe",)):
        role = "CORE_WATCHLIST" if tuple(groups) == ("pcs_universe",) else "CONFIGURED"
        spec = cls.from_symbols(load_market_universe(groups=groups, path=Path(path)), universe_id=str(path))
        return cls(spec.universe_id, spec.symbols, spec.version, role, spec.fingerprint)

    @classmethod
    def from_global_candidates(cls, path: str | Path | None = None):
        source = Path(path) if path is not None else Path("data/artifacts/global_pcs_candidates/active.json")
        if not source.exists():
            audit = Path("data/manifests/daily_universe_migration.csv")
            if path is not None or not audit.exists():
                maximum = int(pd.read_csv(audit, usecols=["symbol"])["symbol"].astype(str).str.upper().nunique()) if audit.exists() else 0
                raise ValueError(f"GLOBAL_UNIVERSE_SOURCE_MISSING:maximum_authorized_population={maximum}:source={audit}")
            build_global_pcs_universe(source=audit)
        pointer = json.loads(source.read_text(encoding="utf-8"))
        snapshot = Path(pointer["snapshot_path"])
        payload = json.loads(snapshot.with_suffix(".json").read_text(encoding="utf-8"))
        executable_symbols = payload.get("included_symbols", payload["symbols"])
        return cls("global_pcs_candidates", tuple(executable_symbols), payload["version"],
                    "GLOBAL_CANDIDATE_UNIVERSE", payload["inventory_fingerprint"])

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


def build_global_pcs_universe(*, source: str | Path = "data/manifests/daily_universe_migration.csv",
                              as_of: str = "latest", config=None,
                              output_directory: str | Path = "data/artifacts/global_pcs_candidates") -> UniverseSpec:
    """Build an immutable global candidate snapshot in the admin boundary."""
    source = Path(source)
    if not source.exists():
        raise ValueError("GLOBAL_UNIVERSE_SOURCE_MISSING")
    raw = pd.read_csv(source, usecols=["symbol", "status", "rows_written", "partitions", "source", "source_size"])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    success = raw[raw.status.astype(str).str.upper().eq("SUCCESS")].copy()
    before = len(success)
    symbols = merge_symbols(explicit_symbols=success.symbol.tolist())
    invalid_count = int(sum(not str(x).strip().replace(".", "").isalnum() for x in success.symbol))
    duplicate_count = before - len(set(str(x).strip().upper() for x in success.symbol))
    if not symbols:
        raise ValueError("GLOBAL_UNIVERSE_SOURCE_MISSING")
    # Reuse the existing BasePoolConfig/static screening implementation only
    # during maintenance; its direct file scan never enters decision reads.
    from pcs.data.base_pool import BasePoolConfig, _underlying_rows
    cfg = config or BasePoolConfig()
    try:
        screened = _underlying_rows(success.rename(columns={"rows_written": "rows_written"}), cfg, None)
    except Exception:
        screened = pd.DataFrame()
    status_by_symbol = {str(r.symbol).upper(): str(r.underlying_status) for r in screened.itertuples()} if len(screened) else {}
    included = [s for s in symbols if status_by_symbol.get(s, "DATA_BLOCKED") == "UNDERLYING_ELIGIBLE"]
    excluded = [s for s in symbols if s not in set(included)]
    inventory_fingerprint = hashlib.sha256("\n".join(symbols).encode()).hexdigest()
    version = f"{source_hash[:16]}-{inventory_fingerprint[:16]}"
    root = Path(output_directory); root.mkdir(parents=True, exist_ok=True)
    snapshot = root / f"global_pcs_candidates_{version}.parquet"
    meta = snapshot.with_suffix(".json")
    manifest = root / f"global_pcs_candidates_{version}_manifest.json"
    frame = pd.DataFrame({"symbol": symbols, "eligibility_status": ["PCS_ELIGIBLE" if s in set(included) else "DATA_BLOCKED" for s in symbols],
                          "reason_codes": ["STATIC_ELIGIBILITY_PASS" if s in set(included) else "STATIC_DATA_UNAVAILABLE" for s in symbols]})
    temporary = snapshot.with_name(snapshot.name + ".tmp"); frame.to_parquet(temporary, index=False); os.replace(temporary, snapshot)
    payload = {"universe_id":"global_pcs_candidates", "universe_role":"GLOBAL_CANDIDATE_UNIVERSE", "version":version,
               "generated_at":pd.Timestamp.now("UTC").isoformat(), "effective_as_of":as_of,
               "source_manifest":str(source), "source_manifest_hash":source_hash,
               "inventory_fingerprint":inventory_fingerprint, "rule_version":"BasePoolConfig-v1",
               "raw_inventory_count":int(len(raw)), "normalized_count":len(symbols), "duplicate_count":duplicate_count,
               "invalid_count":invalid_count, "migration_failed_count":int((~raw.status.astype(str).str.upper().eq("SUCCESS")).sum()),
               "included_symbol_count":len(included), "excluded_symbol_count":len(excluded),
               "symbols":symbols, "included_symbols":included, "excluded_symbols":excluded}
    temporary = meta.with_name(meta.name + ".tmp"); temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"); os.replace(temporary, meta)
    payload["artifact_hash"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    temporary = manifest.with_name(manifest.name + ".tmp"); temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"); os.replace(temporary, manifest)
    pointer = root / "active.json"; temporary = pointer.with_name(pointer.name + ".tmp"); temporary.write_text(json.dumps({"snapshot_path":str(snapshot),"manifest_path":str(manifest),"version":version}, indent=2), encoding="utf-8"); os.replace(temporary, pointer)
    return UniverseSpec("global_pcs_candidates", tuple(symbols), version,
                        "GLOBAL_CANDIDATE_UNIVERSE", inventory_fingerprint)

def resolve_pool_universe(symbols=None, universe_id=None) -> UniverseSpec:
    """Resolve the same explicit or configured population for every pool entrypoint."""
    if symbols is not None:
        return UniverseSpec.from_symbols(symbols, universe_id=universe_id or "explicit")
    if universe_id in {"core_watchlist", "pcs_universe"}:
        spec = UniverseSpec.from_config("config/market_universe.yaml")
        return UniverseSpec(spec.universe_id, spec.symbols, spec.version, "CORE_WATCHLIST", spec.fingerprint)
    if universe_id in {None, "global_pcs_candidates"}:
        return UniverseSpec.from_global_candidates()
    return UniverseSpec.from_file(universe_id)


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
