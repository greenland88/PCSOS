import json
from pathlib import Path

import pytest

from pcs.validation.frozen_dependency import resolve


def config(tmp_path, **changes):
    files = {}
    names = {"rules.yaml": "config/pcs_rules.yaml", "life.parquet": "life.parquet", "split.json": "split.json", "routes.yaml": "config/data_source_routes.yaml", "manifest.csv": "data/manifests/storage_manifest.csv"}
    for key, name in names.items():
        p = tmp_path / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(key); files[key] = p
    import hashlib
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    return {"source_hashes": {str(p): h(p) for p in files.values()}}, files


def test_unrelated_route_change_auto_accept(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c, f = config(tmp_path)
    c["source_hashes"][str(f["routes.yaml"])] = "old"
    out = resolve(c, tmp_path, tool_version="test")
    assert out["status"] == "PASS"


def test_direct_lifecycle_change_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c, f = config(tmp_path)
    c["source_hashes"][str(f["life.parquet"])] = "old"
    with pytest.raises(RuntimeError, match="direct runtime"):
        resolve(c, tmp_path, tool_version="test")


@pytest.mark.parametrize("key", ["split.json", "rules.yaml"])
def test_direct_split_or_rules_change_blocks(tmp_path, monkeypatch, key):
    monkeypatch.chdir(tmp_path)
    c, f = config(tmp_path)
    c["source_hashes"][str(f[key])] = "changed"
    with pytest.raises(RuntimeError, match="direct runtime"):
        resolve(c, tmp_path, tool_version="test")


def test_unrelated_manifest_change_auto_accept(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c, f = config(tmp_path)
    c["source_hashes"][str(f["manifest.csv"])] = "old"
    assert resolve(c, tmp_path, tool_version="test")["status"] == "PASS"


def test_unknown_declared_classification_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c, f = config(tmp_path)
    c["direct_dependencies"] = [{"artifact_name": str(f["life.parquet"]), "classification": "MAYBE"}]
    with pytest.raises(RuntimeError, match="unknown dependency classification"):
        resolve(c, tmp_path, tool_version="test")


def test_sealed_finalizer_has_no_current_source_access():
    source = Path("scripts/finalize_spy_qqq_oos_20260821.py").read_text()
    assert "PCSDataAccess" not in source
    assert "resolve_source" not in source
