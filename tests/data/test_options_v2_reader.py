import pandas as pd
import pytest

from pcs.data.access import DataQualityError, PCSDataAccess


def _routes(manifest, root):
    return {"options": {"by_symbol": {"QQQ": {
        "dataset": "options_v2", "manifest_path": str(manifest), "parquet_root": str(root)
    }}}}


def _frame(symbol="QQQ"):
    return pd.DataFrame([{
        "symbol": symbol, "trade_date": pd.Timestamp("2026-08-03").date(), "expiration_date": pd.Timestamp("2026-08-14").date(),
        "strike": 712.0, "call_put": "c", "last": 5.5, "bid": 5.47, "ask": 5.52,
        "bid_iv": 0.2, "ask_iv": 0.21, "open_interest": 10, "volume": 2,
        "delta": -0.2, "gamma": 0.01, "vega": 0.1, "theta": -0.1, "rho": 0.01,
    }])


def _manifest(path, parquet, row_count=1):
    pd.DataFrame([{
        "dataset": "options_v2", "symbol": "QQQ", "source_file": "fixture",
        "row_count": row_count, "min_date": "2026-08-03", "max_date": "2026-08-03",
        "year": 2026, "quarter": 3, "parquet_path": str(parquet),
        "schema_version": 1, "status": "SUCCESS",
    }]).to_csv(path, index=False)


def test_recursive_collision_prevention_and_manifest_preference(tmp_path):
    root = tmp_path / "data"
    good = root / "options_v2" / "symbol=QQQ" / "year=2026" / "quarter=3" / "good.parquet"
    good.parent.mkdir(parents=True)
    _frame().to_parquet(good, index=False)
    # A descendant that broad recursive discovery would encounter.
    bad = good.parent / "archive" / "bad.parquet"
    bad.parent.mkdir()
    bad_frame = _frame(); bad_frame.loc[0, "bid"] = 5.0
    bad_frame.to_parquet(bad, index=False)
    manifest = tmp_path / "manifest.csv"; _manifest(manifest, good)
    access = PCSDataAccess(source_routes=_routes(manifest, root))
    result = access.read("options", "QQQ", "2026-08-03", "2026-08-03")
    assert len(result) == 1 and result.iloc[0].bid == 5.47
    assert "**" not in access.resolve_source("options", "QQQ", "2026-08-03", "2026-08-03").path


def test_multiple_active_files_fail_clearly(tmp_path):
    root = tmp_path / "data"; part = root / "options_v2" / "symbol=QQQ" / "year=2026" / "quarter=3"
    part.mkdir(parents=True)
    _frame().to_parquet(part / "a.parquet", index=False); _frame().to_parquet(part / "b.parquet", index=False)
    manifest = tmp_path / "manifest.csv"; _manifest(manifest, part / "a.parquet")
    with pytest.raises(DataQualityError, match="multiple active option files"):
        PCSDataAccess(source_routes=_routes(manifest, root)).resolve_source("options", "QQQ", "2026-08-03", "2026-08-03")


def test_manifest_driven_v2_route_without_symbol_override(tmp_path):
    root = tmp_path / "data"
    good = root / "options_v2" / "symbol=META" / "year=2026" / "quarter=3" / "meta.parquet"
    good.parent.mkdir(parents=True)
    frame = _frame("META")
    frame.to_parquet(good, index=False)
    manifest = tmp_path / "manifest.csv"
    pd.DataFrame([{
        "dataset": "options_v2", "symbol": "META", "source_file": "fixture",
        "row_count": 1, "min_date": "2026-08-03", "max_date": "2026-08-03",
        "year": 2026, "quarter": 3, "parquet_path": str(good),
        "schema_version": 1, "status": "SUCCESS",
    }]).to_csv(manifest, index=False)
    access = PCSDataAccess(manifest_path=manifest, parquet_root=root)
    resolved = access.resolve_source("options_v2", "META")
    assert resolved.dataset == "options_v2"
    assert access.read("options_v2", "META", "2026-08-03", "2026-08-03").shape[0] == 1
