"""Create an immutable, isolated SPY/QQQ OOS configuration manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path("research_outputs/spy_qqq_pcs_baseline_20260821")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    files = [
        Path("config/pcs_rules.yaml"), Path("config/data_source_routes.yaml"),
        Path("data/manifests/storage_manifest.csv"), Path("data/manifests/storage_manifest_options_v2.csv"),
        Path("data/derived/market_confirmation_daily.parquet"),
        Path("data/derived/market_confirmation_daily.validation.json"),
        Path("data/derived/market_confirmation_daily.provenance.json"),
        ROOT / "SPY_entry_contract_v2.parquet", ROOT / "QQQ_entry_contract_v2.parquet",
        ROOT / "SPY_lifecycle_marks.parquet", ROOT / "QQQ_lifecycle_marks.parquet",
        ROOT / "split_manifest.json",
    ]
    missing = [str(p) for p in files if not p.exists()]
    if missing:
        raise FileNotFoundError(missing)
    hashes = {str(p).replace("\\", "/"): sha(p) for p in files}
    direct_names = [str(p).replace("\\", "/") for p in files if "routes" not in str(p) and "manifest" not in str(p)]
    transitive_names = [str(p).replace("\\", "/") for p in files if p in {Path("config/data_source_routes.yaml"), Path("data/manifests/storage_manifest.csv"), Path("data/manifests/storage_manifest_options_v2.csv")}]
    manifest = {
        "module": "spy_qqq_immutable_oos_config",
        "version": "20260821.v1",
        "config_id": "PCS-SPY-QQQ-OOS-FROZEN-20260821-V1",
        "protected_config_unchanged": "PCS-OOS-FROZEN-20260821-V1",
        "symbols": ["SPY", "QQQ"],
        "baseline_rules": "PCS-OOS-FROZEN-20260821-V1",
        "safe_strike_atr": 2.3,
        "company_earnings_gate": "NOT_APPLICABLE",
        "market_confirmation_contract": "SPY_QQQ_MARKET_CONFIRMATION",
        "final_oos_run": False,
        "parameter_search": False,
        "source_hashes": hashes,
        "direct_dependencies": [{"artifact_name": n, "artifact_hash": hashes[n], "classification": "DIRECT_RUNTIME_INPUT", "semantic_role": "sealed FINAL OOS runtime input"} for n in direct_names],
        "transitive_provenance": [{"source_name": n, "source_hash": hashes[n], "classification": "TRANSITIVE_PROVENANCE_ONLY", "semantic_scope": "SPY/QQQ provenance only", "ticker_scope": ["SPY", "QQQ"]} for n in transitive_names],
        "sealed_finalization_supported": "YES",
    }
    (ROOT / "immutable_oos_config.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
