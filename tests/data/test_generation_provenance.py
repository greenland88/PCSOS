import hashlib
import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess, DataAccessError, DataQualityError
from pcs.data.canonical_generations import register_active_generation_provenance


def _access(tmp_path):
    return PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")


def _daily(symbol="ZZZ"):
    return pd.DataFrame({"symbol": [symbol, symbol], "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                         "open": [1.0, 1.0], "high": [1.1, 1.1], "low": [.9, .9],
                         "close": [1.0, 1.0], "volume": [10, 10]})


def _registered(tmp_path, symbol="ZZZ", dataset="daily"):
    a = _access(tmp_path)
    r = a.promote_generation(_daily(symbol), dataset, symbol, "year=2024", source_version="fixture")
    return a, r


def test_provenance_registration_exact_row_success(tmp_path):
    a, r = _registered(tmp_path)
    out = register_active_generation_provenance(dataset="daily", symbol="ZZZ", generation_id=r.generation_id,
                                                price_basis="canonical_adjusted", corporate_action_version="canonical_identity", data_access=a)
    assert out["generation_id"] == r.generation_id and out["dataset_fingerprint"]


def test_provenance_registration_different_symbol_and_dataset_isolated(tmp_path):
    a, r = _registered(tmp_path, "ZZZ")
    with pytest.raises(DataAccessError):
        register_active_generation_provenance(dataset="daily", symbol="YYY", generation_id=r.generation_id, price_basis="x", corporate_action_version="y", data_access=a)
    with pytest.raises(DataAccessError):
        register_active_generation_provenance(dataset="options", symbol="ZZZ", generation_id=r.generation_id, price_basis="x", corporate_action_version="y", data_access=a)


def test_provenance_registration_manifest_stale(tmp_path, monkeypatch):
    a, r = _registered(tmp_path)
    original = a._read_manifest
    def changed(path):
        out = original(path)
        a.manifest_path.write_bytes(a.manifest_path.read_bytes() + b"\n")
        return out
    monkeypatch.setattr(a, "_read_manifest", changed)
    with pytest.raises(DataAccessError, match="PROVENANCE_PLAN_STALE"):
        register_active_generation_provenance(dataset="daily", symbol="ZZZ", generation_id=r.generation_id, price_basis="x", corporate_action_version="y", data_access=a)


def test_provenance_registration_checksum_error(tmp_path):
    a, r = _registered(tmp_path)
    m = pd.read_csv(a.manifest_path); m.loc[0, "content_hash"] = "bad"; m.to_csv(a.manifest_path, index=False)
    with pytest.raises(DataQualityError, match="CONTENT_HASH_MISMATCH"):
        register_active_generation_provenance(dataset="daily", symbol="ZZZ", generation_id=r.generation_id, price_basis="x", corporate_action_version="y", data_access=a)

@pytest.mark.parametrize("field,value", [("schema_version", float("nan")), ("price_basis", ""), ("corporate_action_version", "")])
def test_provenance_registration_requires_complete_identity_fields(tmp_path, field, value):
    a, r = _registered(tmp_path); m = pd.read_csv(a.manifest_path); m.loc[0, field] = value; m.to_csv(a.manifest_path, index=False)
    with pytest.raises(DataAccessError, match="DATASET_PROVENANCE_INCOMPLETE"):
        register_active_generation_provenance(dataset="daily", symbol="ZZZ", generation_id=r.generation_id, price_basis="x", corporate_action_version="y", data_access=a)


def test_provenance_registration_atomic_write_failure_preserves_bytes(tmp_path, monkeypatch):
    a, r = _registered(tmp_path); before = a.manifest_path.read_bytes()
    import pcs.data.canonical_generations as cg
    monkeypatch.setattr(cg.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError):
        register_active_generation_provenance(dataset="daily", symbol="ZZZ", generation_id=r.generation_id, price_basis="x", corporate_action_version="y", data_access=a)
    assert a.manifest_path.read_bytes() == before


def test_provenance_registration_does_not_change_active_pointer(tmp_path):
    a, r = _registered(tmp_path); before = pd.read_csv(a.manifest_path).iloc[0]
    register_active_generation_provenance(dataset="daily", symbol="ZZZ", generation_id=r.generation_id, price_basis="x", corporate_action_version="y", data_access=a)
    after = pd.read_csv(a.manifest_path).iloc[0]
    assert after.active_generation == before.active_generation == r.generation_id
    assert (pd.isna(after.previous_generation) and pd.isna(before.previous_generation)) or after.previous_generation == before.previous_generation
