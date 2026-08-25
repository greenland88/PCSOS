from pathlib import Path

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

