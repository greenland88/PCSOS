from pathlib import Path
import pandas as pd

from pcs.data.access import PCSDataAccess


def test_nvda_logical_options_resolves_to_v3():
    spec = PCSDataAccess().resolve_source("options", "NVDA")
    assert spec.dataset == "options_v3"
    assert "storage_manifest_options_v3.csv" in spec.source_version


def test_jpm_logical_options_resolves_to_v2():
    spec = PCSDataAccess().resolve_source("options", "JPM")
    assert spec.dataset == "options_v2"
    assert "storage_manifest_options_v2.csv" in spec.source_version


def test_logical_route_evidence_is_version_neutral():
    from pcs.data.readiness import canonical_route_evidence

    for ticker in ("NVDA", "JPM"):
        evidence = canonical_route_evidence(PCSDataAccess(), ticker)
        assert evidence["spec"]["symbol"] == ticker
        assert evidence["requested_dataset"] == "options"


def test_manifest_only_v2_options_route_without_by_symbol_entry(tmp_path):
    manifest = tmp_path / "storage_manifest.csv"
    pd.DataFrame([{
        "dataset": "options_v2", "symbol": "META", "status": "SUCCESS",
        "row_count": 1, "min_date": "2026-01-02", "max_date": "2026-01-02",
        "year": 2026, "quarter": 1,
        "parquet_path": str(tmp_path / "options_v2/symbol=META/year=2026/quarter=1/META.parquet"),
        "schema_version": "1",
    }]).to_csv(manifest, index=False)
    access = PCSDataAccess(parquet_root=tmp_path, source_routes={"options": {"by_symbol": {}}})
    fixture = pd.read_csv(manifest)
    access._manifest = fixture
    access._read_manifest = lambda path: fixture if path.name == "storage_manifest_options_v2.csv" else pd.DataFrame()
    resolved_dataset, resolved_manifest, _ = access._resolve_route("options", "META")
    assert resolved_dataset == "options_v2"
    assert resolved_manifest.name == "storage_manifest_options_v2.csv"
